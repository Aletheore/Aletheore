import hashlib
import re
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import lancedb
from lancedb.index import FTS
from openai import OpenAI

from aletheore.credentials import DEFAULT_CREDENTIALS_PATH, get_api_key, has_api_key

FALLBACK_CHUNK_MAX_LINES = 200
DEFAULT_EMBEDDING_BASE_URL = "http://localhost:11434/v1"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
OPENAI_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"

# Aletheore's own embedding endpoint, used when a saved API token belongs to
# an entitled installation. It serves the same OPENAI_EMBEDDING_MODEL, so a
# user switching between hosted and their own key keeps the same 1536
# dimensions and does not trigger the full re-embed a dimension change
# forces. Local Ollama does not - nomic-embed-text is 768 - so that switch
# does rebuild, correctly.
HOSTED_EMBEDDING_PATH = "/v1/embeddings"
DEFAULT_API_BASE_URL = "https://app.aletheore.com"
INDEX_DIRNAME = "index.lancedb"
TABLE_NAME = "chunks"


class EmbeddingProviderUnavailableError(Exception):
    pass


class IndexNotFoundError(Exception):
    pass


def _default_confirm_openai_fallback() -> bool:
    print(
        "Ollama is unavailable. Aletheore can fall back to OpenAI's "
        f"'{OPENAI_EMBEDDING_MODEL}' embeddings API instead - this sends this "
        "repository's source code chunks to OpenAI's API."
    )
    return input("Continue with OpenAI embeddings? [y/N]: ").strip().lower() == "y"


# How much of a file's pre-symbol head (docstring, imports, top-level
# constants) goes into its module chunk. Generous enough for a real module
# docstring, bounded so a file with a 2,000-line lookup table before its
# first function doesn't produce one enormous chunk that matches everything.
MODULE_CHUNK_MAX_LINES = 80

# nomic-embed-text has a hard 2048-token training context, and the hosted
# side already learned this the expensive way: scan_worker/embedding_client.py
# records that 6600 chars succeeded and 6990 failed against the real model,
# and that its cache sat at a 0% hit rate for 38 hours before anyone noticed
# every call was failing. 5000 keeps that same margin. The constant is
# restated rather than imported because src/ must not depend on github-app/ -
# the dependency runs the other way.
#
# Truncating rather than skipping: a genuinely large function should still be
# findable by its opening, which is where the signature and docstring are.
MAX_EMBEDDING_CHARS = 5000

# Directories and suffixes whose contents are not this repository's code.
# Measured on this repo: one minified bundle (website/vendor/motion.js) was
# 98% of the entire index's embedding cost - tree-sitter finds 271 "functions"
# in it, every one spanning lines 1-1, so each chunk contained the whole
# 44,883-token file and it was embedded 271 times over. Nobody asks questions
# about a vendored bundle, and the line-based MODULE_CHUNK_MAX_LINES cap is no
# defense because the file is a single line.
_VENDOR_DIR_MARKERS = frozenset(
    {"vendor", "vendored", "third_party", "thirdparty", "external", "dist", "bundles"}
)
_MINIFIED_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".bundle.css", "-min.js")


def _is_vendored_path(module_path: str) -> bool:
    parts = module_path.split("/")
    if any(part in _VENDOR_DIR_MARKERS for part in parts[:-1]):
        return True
    return parts[-1].endswith(_MINIFIED_SUFFIXES)


FILE_CONTEXT_MAX_CHARS = 300

_COMMENT_PREFIXES = ("#", "//", "///", "/*", "*", '"""', "'''", "--", ";;")

_LEGAL_NOISE = re.compile(
    r"copyright|all rights reserved|licen[sc]e|spdx|BSD|MIT |Apache|redistribution"
    # A bare Javadoc/PHPDoc tag line - @license is already caught above via
    # "licen[sc]e", but @author/@package/@link/@copyright/@api carry no
    # legal keyword of their own and slipped through untouched. slimphp/Slim
    # closes its banner with "@api */" on one line, which this catches at
    # the tag; the trailing "*/" is stripped separately below regardless.
    r"|^@(author|package|link|copyright|api)\b"
    # A line that is only a bare URL - the boilerplate part of a banner like
    # "Slim Framework (https://slimframework.com)" once the parenthetical
    # is split out, and common on its own as a licence-file pointer.
    r"|^https?://\S+$",
    re.IGNORECASE,
)
# The other half of that same banner shape: a project name followed by a
# parenthesised URL, with no legal keyword in it at all - "Slim Framework
# (https://slimframework.com)" reads as ordinary prose to _LEGAL_NOISE
# above, and was the single most common leaked string measured on
# slimphp/Slim (121 of 455 chunks). The cross-file repeat-count guard in
# build_chunks is the durable backstop for banners this regex doesn't
# anticipate; this catches the common shape without waiting for a second
# file to prove it's boilerplate.
_PROJECT_BANNER = re.compile(r"\([^)]*https?://[^)]*\)\s*$")

# The first of these means later comments describe that symbol, not the file.
_DEFINITION_START = re.compile(
    r"^(pub\s+)?(async\s+)?(export\s+)?(default\s+)?"
    r"(def|class|fn|func|impl|struct|enum|trait|interface|type|var|let|const|"
    r"function|module|abstract|final|public|private|protected|static|template)\b"
)


def _file_header_comment(lines: list[str]) -> str:
    """The file's leading comment/docstring text, flattened to one line.

    Stops at the first run of real code, so a mid-file comment never leaks in
    and a file whose header is only imports contributes nothing.
    """
    collected: list[str] = []
    # Set while inside a multi-line triple-quoted docstring, to the
    # delimiter that opened it. A standard Python module docstring's body
    # lines carry no per-line comment marker of their own - without this,
    # every body line falls through to "not a comment, not a definition"
    # and the whole docstring is silently dropped.
    open_triple_quote: str | None = None
    for raw in lines[:MODULE_CHUNK_MAX_LINES]:
        line = raw.strip()
        if open_triple_quote is not None:
            if not line:
                continue
            close_at = line.find(open_triple_quote)
            text = line[:close_at] if close_at != -1 else line
            text = text.strip()
            if text and not _LEGAL_NOISE.search(text) and not _PROJECT_BANNER.search(text):
                collected.append(text)
            if close_at != -1:
                open_triple_quote = None
            if sum(len(c) for c in collected) >= FILE_CONTEXT_MAX_CHARS:
                break
            continue
        if not line:
            continue
        if line.startswith(('"""', "'''")):
            delim = line[:3]
            rest = line[3:]
            close_at = rest.find(delim)
            if close_at == -1:
                # Opens here with no closing delimiter on this line - the
                # common multi-line module-docstring shape. Collect this
                # line's own trailing text, if any, then keep consuming
                # body lines until the matching close.
                text = rest.strip()
                if text and not _LEGAL_NOISE.search(text):
                    collected.append(text)
                open_triple_quote = delim
            else:
                # Single-line docstring - strip the delimiter from both
                # ends, not just the left, or the closing """ leaks into
                # the indexed text.
                text = rest[:close_at].strip()
                if text and not _LEGAL_NOISE.search(text):
                    collected.append(text)
            if sum(len(c) for c in collected) >= FILE_CONTEXT_MAX_CHARS:
                break
            continue
        if line.startswith(_COMMENT_PREFIXES):
            text = line.lstrip("/#*-!;\"' ").strip()
            # A C-style block comment's closing "*/" only ever shows up on
            # the RIGHT of whatever else is on its line (the lstrip above
            # only ever touches the left), and often rides along with
            # otherwise-harmless trailing content - slimphp/Slim closes its
            # banner with "@api */" on one line. Stripped unconditionally,
            # not just when the line is noise, since a real trailing "*/"
            # on a legitimate doc line is exactly as unwanted.
            text = re.sub(r"\*+/\s*$", "", text).strip()
            # Licence headers are the same on every file in a repo, so they
            # carry no signal and actively dilute the symbol they ride on -
            # gin's files all begin "Copyright 2013 Julien Schmidt".
            if text and not _LEGAL_NOISE.search(text) and not _PROJECT_BANNER.search(text):
                collected.append(text)
            if sum(len(c) for c in collected) >= FILE_CONTEXT_MAX_CHARS:
                break
            continue
        if _DEFINITION_START.match(line):
            # A definition means any comment from here on documents that symbol,
            # not the file. Skipping past it grabs the first class or function
            # docstring and staples that one symbol's description onto every
            # other symbol in the file - measured at Flask top-1 71.9% -> 65.6%.
            break
        # Anything else before the first definition - imports, the continuation
        # lines of a braced import block, attributes - is skipped rather than
        # treated as the end of the header. serde's `/// An efficient way of
        # discarding data...` sits after a multi-line `use crate::de::{...}`
        # block, and stopping at that block's continuation lines lost exactly
        # the sentence worth carrying.
        continue
    return " ".join(collected)[:FILE_CONTEXT_MAX_CHARS]


def _primary_symbol_docstring(module_path: str, classes: list[dict]) -> str:
    """Fallback [file] context for a file with no leading comment of its
    own: the docstring of the class/interface the file is named after -
    its identity under PHP/Java/C#/TypeScript's one-type-per-file
    convention.

    Exists because a header-less file loses ties it should win.
    slimphp/Slim's CallableResolver.php goes straight from
    declare(strict_types=1) to namespace to use - no header at all - while
    the four sibling __invoke methods that lexically collide with a
    question about "something invokable" all sit in files with a header
    docblock. _file_header_comment correctly returns "" for
    CallableResolver.php; leaving it there means the file with no context
    loses to files that have some, even when it's the right answer.

    Matched by name against the file's own stem, not "the first symbol in
    the file" - restricted this way so it can never re-introduce the bug
    _DEFINITION_START exists to prevent (stapling one symbol's docstring
    onto every other symbol in the file). A file with several classes and
    none matching its own name gets no fallback, same as today.
    """
    stem = Path(module_path).stem
    for cls in classes:
        if cls.get("name") == stem:
            return cls.get("docstring") or ""
    return ""


def _truncate_for_embedding(text: str) -> str:
    if len(text) <= MAX_EMBEDDING_CHARS:
        return text
    # Marked rather than silently cut, so a reader of the returned chunk can
    # tell the difference between a short symbol and a clipped one.
    return text[:MAX_EMBEDDING_CHARS] + "\n... (truncated for embedding)"


def _is_test_path(module_path: str) -> bool:
    """Whether a path is test code rather than the implementation.

    Measured on this repo: tests were 485 of 793 indexed chunks (61%) and
    took 64% of all top-5 result slots, because a test shares its subject's
    identifiers and domain vocabulary while outnumbering it. Retrieval
    accuracy for "how does X work" went from 45% to 68% top-5 with these
    excluded. Someone asking how something works wants the implementation;
    if they want the test, they ask for the test by name and grep finds it.
    """
    parts = [part.lower() for part in module_path.split("/")]
    if any(part in {"tests", "test", "spec", "__tests__", "testing"} for part in parts):
        return True
    # .NET names test projects after the assembly they cover - "UnitTests",
    # "IntegrationTests", "AutoMapper.DI.Tests" - none of which is an exact
    # match for the segments above, and none of which was being excluded.
    # Measured on AutoMapper/AutoMapper: every one of 15 questions returned
    # src/UnitTests/ files ahead of the implementation, for 0.0% top-1.
    #
    # Matched on the "tests" plural only. "test" as a suffix would swallow
    # ordinary words - "latest" ends with "test" - while no English word this
    # matters for ends with "tests".
    if any(part.endswith("tests") or part.endswith(".test") for part in parts):
        return True
    name = parts[-1]
    stem = name.rsplit(".", 1)[0]
    return (
        stem == "conftest"
        or stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
    )


# Directories a PHP, Java, or C# codebase commonly puts every interface under
# regardless of what the file itself contains - a path-level signal that
# needs no content inspection at all.
_INTERFACE_DIR_MARKERS = frozenset({"interfaces", "contracts"})

_PHP_INTERFACE_DECL = re.compile(r"^\s*interface\s+\w", re.MULTILINE)
_JAVA_CSHARP_INTERFACE_DECL = re.compile(r"^\s*(?:public\s+|internal\s+)?interface\s+\w", re.MULTILINE)
_RUST_TRAIT_DECL = re.compile(r"^\s*(?:pub\s+)?trait\s+\w", re.MULTILINE)
# A trait method WITH a default body reads `fn name(...) { ... }`; a bare
# signature ends in `;`. One match anywhere in the file is enough to treat
# the trait as having real behaviour, not just a contract.
_RUST_METHOD_WITH_BODY = re.compile(r"\bfn\s+\w[^;{}]*\{")
# Same idea for a C/C++ header: a function DEFINITION closes its parameter
# list with `{`, a prototype with `;`.
_C_FUNCTION_DEFINITION = re.compile(r"\)\s*(?:const\s*)?\{")
# TypeScript: `interface Foo` or `type Foo = ...` - either is pure API
# surface. Matched separately from any real implementation below, because
# unlike PHP/Java/C# a .ts file routinely mixes both (a types-and-helpers
# module), and only the ones with neither should be treated as pure
# contract - colinhacks/zod's enumUtil.ts is the case this exists for:
# entirely `type X = ...` inside a namespace, no function or class at all.
_TS_TYPE_DECL = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:interface|type)\s+\w", re.MULTILINE)
_TS_CLASS_OR_FUNCTION_DECL = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?(?:class|function)\s+\w",
    re.MULTILINE,
)
# `const f = () => {}` / `const f = function() {}` - see graph.py's own
# _extract_javascript, which extracts exactly this shape as a function.
_TS_ASSIGNED_FUNCTION = re.compile(
    r"=\s*(?:async\s+)?(?:\([^()]*\)|\w+)\s*(?::[^=]+)?=>|=\s*(?:async\s+)?function\b"
)


def _is_declaration_only_file(module_path: str, language: str, source: str) -> bool:
    """Whether a file is pure API surface with no implementation behind it.

    Measured on slimphp/Slim: interfaces were 17 of 72 PHP files (24%) and
    took 18 of 75 top-5 slots (24%), displacing the correct answer on 4 of 6
    misses. A declaration-only file is pure contract - rich doc-comments
    describing behaviour, nothing implementing it to dilute them - which
    makes it unusually attractive to an embedder for "how does X work" when
    the reader wants the implementation, not the interface. Same reasoning
    as _is_test_path (a test shares its subject's vocabulary while
    outnumbering it), but this is a demotion, not an exclusion: unlike a
    test, an interface is legitimately the answer to "where is the contract
    for X defined?" - see the rank penalty in _rrf_fuse.

    Known gaps, left for a real corpus to surface before chasing them: Java
    8+ `default` interface methods (an interface CAN have a body there), Go
    interfaces (usually not split into their own file by convention, unlike
    PHP/Java/C#), and PHP abstract classes (deliberately not treated as
    declaration-only - unlike `interface`, PHP's `abstract class` routinely
    mixes abstract and fully-implemented methods in the same file).
    """
    parts = module_path.split("/")
    if any(part.lower() in _INTERFACE_DIR_MARKERS for part in parts[:-1]):
        return True
    name = parts[-1]
    if name.endswith(".d.ts"):
        return True
    if language == "php":
        return bool(_PHP_INTERFACE_DECL.search(source))
    if language in ("java", "csharp"):
        return bool(_JAVA_CSHARP_INTERFACE_DECL.search(source))
    if language == "rust":
        return bool(_RUST_TRAIT_DECL.search(source)) and not _RUST_METHOD_WITH_BODY.search(source)
    if language == "cpp" and name.endswith((".h", ".hpp")):
        return not _C_FUNCTION_DEFINITION.search(source)
    if language == "typescript":
        return bool(_TS_TYPE_DECL.search(source)) and not (
            _TS_CLASS_OR_FUNCTION_DECL.search(source) or _TS_ASSIGNED_FUNCTION.search(source)
        )
    return False


_BOILERPLATE_MIN_REPEAT_COUNT = 2


def build_chunks(evidence: dict, repo_path: Path) -> list[dict]:
    # First pass: read every file once, computing its [file] context and
    # tallying how often each distinct context string recurs across the
    # repo. A context shared by more than _BOILERPLATE_MIN_REPEAT_COUNT
    # files is boilerplate by definition - a licence banner, a
    # generated-file notice, a copyright block the regex filters above
    # didn't anticipate - not a coincidence. Measured on slimphp/Slim: 121
    # of 455 chunks carried the identical "Slim Framework
    # (https://slimframework.com) @api */" string, stamping the same 50
    # characters of noise onto a quarter of the index instead of the
    # distinguishing sentence this feature exists to add. Deterministic and
    # language-agnostic where the regex filters are neither, and it catches
    # banners no regex anticipated - the durable fix; the regexes above are
    # the quick one.
    pending: list[tuple[dict, list[str], str]] = []
    context_counts: dict[str, int] = {}
    for module in evidence["repository"]["modules"]:
        module_path = module["path"]
        if _is_test_path(module_path) or _is_vendored_path(module_path):
            continue
        file_path = repo_path / module_path
        if not file_path.exists():
            continue
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        file_context = _file_header_comment(lines)
        if not file_context:
            # A header-less file loses ties it should win: slimphp/Slim's
            # CallableResolver.php has no file-level comment at all, while
            # the sibling __invoke methods it's competing against (a
            # lexical match on a question about "something invokable") sit
            # in files that do - so the actually-correct file was the only
            # one of the two with no disambiguating context. See
            # _primary_symbol_docstring.
            file_context = _primary_symbol_docstring(
                module_path, module["symbols"]["classes"]
            )
        if file_context:
            context_counts[file_context] = context_counts.get(file_context, 0) + 1
        pending.append((module, lines, file_context))

    boilerplate_contexts = {
        context for context, count in context_counts.items()
        if count > _BOILERPLATE_MIN_REPEAT_COUNT
    }

    # How many distinct files declare each symbol name, so the [file] context
    # can be spent only where it does work - see the gate in the symbol loop.
    symbol_file_counts: dict[str, int] = {}
    for pending_module, _pending_lines, _pending_context in pending:
        declared = {
            symbol["name"]
            for symbol in pending_module["symbols"]["functions"]
            + pending_module["symbols"]["classes"]
        }
        for name in declared:
            symbol_file_counts[name] = symbol_file_counts.get(name, 0) + 1

    chunks: list[dict] = []
    for module, lines, file_context in pending:
        module_path = module["path"]
        if file_context in boilerplate_contexts:
            file_context = ""

        # Structural metadata straight from AIR, attached to every chunk of
        # this file. Free - the scan already computed it - and it turns
        # LanceDB into something you can pre-filter rather than only rank:
        # a polyglot repo (a Python backend beside a TypeScript frontend
        # beside Terraform) otherwise ranks a matching TS chunk against a
        # Python question with nothing to separate them but embedding
        # distance. `imports` rides along unfiltered because it costs
        # nothing and saves the agent a follow-up aletheore_imports call.
        language = module.get("language", "unknown")
        imports = module.get("imports", [])

        # Computed once per file, attached to every chunk below - see
        # _is_declaration_only_file and the rank penalty in _rrf_fuse.
        is_declaration_only = _is_declaration_only_file(module_path, language, "\n".join(lines))

        code_symbols = module["symbols"]["functions"] + module["symbols"]["classes"]
        # Constants are indexed only for files that define nothing else.
        #
        # Measured both ways. Indexing them everywhere cost accuracy on files
        # that already had functions and classes: on Flask the declarations
        # chunk of signals.py, which contains `appcontext_pushed` and
        # `appcontext_popped`, out-matched ctx.py for "the object pushed onto
        # and popped off a stack", dropping top-1 from 75.0% to 71.9%. But
        # skipping them entirely leaves declaration-only files unreachable, and
        # that is not a Python corner case - a CommonJS or Go module that is all
        # `const` had no indexable content at all.
        #
        # Restricting to files with no other symbols keeps both: a file that
        # already has code is indexed exactly as before, and a file that would
        # otherwise be empty becomes findable.
        constants = []
        if not code_symbols:
            constants = [
                c for c in module["symbols"].get("constants", []) if c.get("is_public", True)
            ]
        symbols = code_symbols + constants
        if not symbols:
            end_line = min(len(lines), FALLBACK_CHUNK_MAX_LINES)
            snippet = "\n".join(lines[:FALLBACK_CHUNK_MAX_LINES])
            chunks.append(
                {
                    "module_path": module_path,
                    "symbol_name": None,
                    "start_line": 1,
                    "end_line": end_line,
                    "language": language,
                    "imports": imports,
                    "is_declaration_only": is_declaration_only,
                    "text": _truncate_for_embedding(f"{module_path} (no extracted symbols)\n{snippet}"),
                }
            )
            continue

        # One chunk describing the module itself, before the per-symbol ones.
        # Without it nothing in the index answers "what is this file for" -
        # every chunk was a single function, so "how is the dependency graph
        # built" matched private helpers like _rel and _symbol_entry and
        # missed graph.py entirely. Measured on this repo: graph.py had 55
        # chunks, the earliest starting at line 66, leaving the module
        # docstring, imports and the LANGUAGE_BY_EXTENSION table unindexed -
        # and only 5 of 793 chunks described a whole module at all.
        #
        # The head of a file is where a docstring, imports and top-level
        # constants live, so it is both the best summary available and the
        # part symbol extraction structurally cannot reach.
        head_end = min(symbols[0]["start_line"] - 1, MODULE_CHUNK_MAX_LINES)
        if head_end > 0:
            head = "\n".join(lines[:head_end]).strip()
            if head:
                symbol_names = ", ".join(s["name"] for s in symbols[:40])
                chunks.append(
                    {
                        "module_path": module_path,
                        # None marks a module chunk, matching the symbol-less
                        # fallback above so consumers need no new concept.
                        "symbol_name": None,
                        "start_line": 1,
                        "end_line": head_end,
                        "language": language,
                        "imports": imports,
                        "is_declaration_only": is_declaration_only,
                        # Path and symbol list join the docstring so the chunk
                        # is reachable by what the module *contains*, not only
                        # by how its author happened to describe it.
                        "text": _truncate_for_embedding(
                            f"{module_path} (module overview)\n{head}\n\ndefines: {symbol_names}"
                        ),
                    }
                )

        for symbol in code_symbols:
            start_line = symbol["start_line"]
            end_line = symbol["end_line"]
            source = "\n".join(lines[start_line - 1:end_line])
            header = f"{module_path}::{symbol['name']} ({language})"
            # Only a symbol whose name is declared in more than one file gets
            # the file's context. The feature exists to break ties between
            # near-identical chunks - serde declares `deserialize` in 57 files,
            # slimphp/Slim declares `__invoke` in four - and a name unique to
            # one file has no tie to break. Spent on every symbol instead, the
            # same sentence is repeated across every chunk of a file and
            # dilutes each symbol's own text: measured across four corpora,
            # attaching it unconditionally cost pallets/flask 3.1 points of
            # top-1 (68.8% -> 65.6%) and gin-gonic/gin 6.7 of top-3, while
            # gating it here keeps the whole of serde's gain, which is where
            # the context earns its keep (33.3% -> 53.3% top-1 against no
            # context at all). No corpus measured regressed on any metric.
            if file_context and symbol_file_counts.get(symbol["name"], 0) > 1:
                header = f"{header}\n[file] {file_context}"
            chunks.append(
                {
                    "module_path": module_path,
                    "symbol_name": symbol["name"],
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": language,
                    "imports": imports,
                    "is_declaration_only": is_declaration_only,
                    "text": _truncate_for_embedding(f"{header}\n{source}"),
                }
            )

        # Module-level bindings go into ONE chunk, not one chunk each. Measured
        # on Flask's signals.py: emitting a chunk per constant produced eleven
        # ~60-character chunks like `template_rendered = _signals.signal(...)`,
        # each too thin to carry meaning on its own, and together they diluted
        # the file's representation enough to push a genuinely better chunk off
        # the top of an unrelated query. Grouped, the declarations read as the
        # single API surface they actually are.
        if constants:
            start_line = min(c["start_line"] for c in constants)
            end_line = max(c["end_line"] for c in constants)
            declared = "\n".join(
                line for line in lines[start_line - 1:end_line] if line.strip()
            )
            names = ", ".join(c["name"] for c in constants[:40])
            chunks.append(
                {
                    "module_path": module_path,
                    "symbol_name": None,
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": language,
                    "imports": imports,
                    "is_declaration_only": is_declaration_only,
                    "text": _truncate_for_embedding(
                        f"{module_path} (module-level declarations)\n"
                        f"declares: {names}\n{declared}"
                    ),
                }
            )

    return chunks


class HostedEmbeddingUnavailableError(Exception):
    """The hosted endpoint refused or could not be reached.

    Distinct from EmbeddingProviderUnavailableError because the caller's
    recourse is different: this one is answered by upgrading, checking a
    token, or falling back to a local provider, not by starting Ollama.
    """


def _repo_id(repo_path: Path) -> str:
    """Stable, opaque identifier for a repository, derived from its resolved
    local path.

    Lets the hosted embeddings rate limit key on (installation, repo)
    instead of only installation: `aletheore watch` running against several
    repos on one token would otherwise share a single request budget, and
    one repo's rebase-heavy burst could starve the others. Never the raw
    path - just a bucket the server can count requests against without
    learning anything about the caller's filesystem.
    """
    return hashlib.sha256(str(repo_path.resolve()).encode("utf-8")).hexdigest()[:16]


def embed_texts_hosted(
    texts: list[str],
    token: str,
    api_base_url: str = DEFAULT_API_BASE_URL,
    http_client: httpx.Client | None = None,
    repo_id: str | None = None,
) -> list[list[float]]:
    """Embed via Aletheore's endpoint using a saved API token.

    The entitlement decision belongs to the server: a 402 here is the gate,
    and the CLI's job is to report what the server said rather than to
    pre-judge it locally. A client-side plan check in an open-source binary
    is a suggestion; this is the real thing, and it also means a plan that
    changes mid-session is honoured without the CLI knowing anything.
    """
    client = http_client or httpx.Client(base_url=api_base_url, timeout=120.0)
    body: dict = {"texts": texts}
    if repo_id:
        body["repo_id"] = repo_id
    try:
        response = client.post(
            HOSTED_EMBEDDING_PATH,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as exc:
        raise HostedEmbeddingUnavailableError(
            f"could not reach Aletheore's embedding service ({type(exc).__name__})"
        ) from exc

    if response.status_code == 402:
        raise HostedEmbeddingUnavailableError(_detail_of(response))
    if response.status_code == 401:
        raise HostedEmbeddingUnavailableError(
            "this API token was rejected - run 'aletheore login' again"
        )
    if response.status_code != 200:
        raise HostedEmbeddingUnavailableError(
            f"Aletheore's embedding service returned {response.status_code}: {_detail_of(response)}"
        )
    return response.json()["vectors"]


def _detail_of(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", "")) or response.reason_phrase
    except ValueError:
        return response.reason_phrase


def embed_texts(
    texts: list[str],
    base_url: str = DEFAULT_EMBEDDING_BASE_URL,
    model: str = DEFAULT_EMBEDDING_MODEL,
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
    confirm_fn: Callable[[], bool] | None = None,
) -> list[list[float]]:
    client = OpenAI(base_url=base_url, api_key="not-needed")
    try:
        response = client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in response.data]
    except Exception as ollama_exc:
        ollama_hint = (
            f"could not reach embedding model '{model}' at {base_url} "
            f"({type(ollama_exc).__name__}) - try 'ollama pull {model}' and confirm "
            "ollama is running"
        )
        if not has_api_key("OPENAI_API_KEY", "OpenAI", credentials_path):
            raise EmbeddingProviderUnavailableError(ollama_hint) from ollama_exc

        if not sys.stdin.isatty():
            raise EmbeddingProviderUnavailableError(
                f"{ollama_hint}. An OPENAI_API_KEY is configured and could be used as a "
                "fallback, but this isn't an interactive terminal, so Aletheore won't send "
                "code to OpenAI without being asked - run this from a real terminal to be "
                "prompted."
            ) from ollama_exc

        confirm = confirm_fn if confirm_fn is not None else _default_confirm_openai_fallback
        if not confirm():
            raise EmbeddingProviderUnavailableError(
                "Ollama is unavailable and the OpenAI embeddings fallback was declined - "
                "no data was sent."
            ) from ollama_exc

        api_key = get_api_key("OPENAI_API_KEY", "OpenAI", credentials_path)
        openai_client = OpenAI(base_url=OPENAI_EMBEDDING_BASE_URL, api_key=api_key)
        try:
            response = openai_client.embeddings.create(
                model=OPENAI_EMBEDDING_MODEL, input=texts
            )
        except Exception as openai_exc:
            raise EmbeddingProviderUnavailableError(
                f"Ollama unavailable ({type(ollama_exc).__name__}) and OpenAI embeddings "
                f"also failed ({type(openai_exc).__name__}) - confirm ollama is running or "
                "OPENAI_API_KEY is valid"
            ) from openai_exc
        return [item.embedding for item in response.data]


def _escape_sql_literal(value: str) -> str:
    """Single quotes doubled, for a value going into a LanceDB where clause.

    The value reaches here from an MCP tool argument, so it is caller-
    supplied even though the caller is usually an agent rather than a person.
    """
    return value.replace("'", "''")


# Chunks per embedding request. One request for the whole repo is what the
# code did before, and it fails: Ollama returned
# `Post "/tokenize": EOF` on a 1,535-chunk repo while 634 and 510 both
# succeeded, so the ceiling sits inside the range of ordinary repositories
# and indexing died outright rather than running slowly.
#
# 200 rather than as-large-as-works: measured throughput is flat from 200
# upward (16.1 ms/chunk at 200, 16.0 at 500), so a bigger batch buys nothing
# and only moves the failure point back to where the next-larger repo finds
# it.
EMBED_BATCH_SIZE = 200


def _embed_in_batches(
    texts: list[str], batch_size: int = EMBED_BATCH_SIZE, repo_id: str | None = None
) -> list[list[float]]:
    """Embed everything, preferring Aletheore's endpoint when entitled.

    Hosted first, then local, and never the reverse: someone paying for
    hosted embeddings should not silently have their code sent to their own
    OpenAI account instead.

    The fallback is only allowed before the first batch succeeds. After that,
    switching providers mid-run would mix 1536-dimension vectors with 768-
    dimension ones in a single index, which LanceDB rejects outright - so a
    hosted failure partway through is raised rather than worked around. A
    half-built index that errors is recoverable; one built from two models
    is not.

    repo_id: forwarded to embed_texts_hosted so the hosted rate limit can be
    keyed per repo rather than per installation - see _repo_id.
    """
    token = get_api_key(
        "ALETHEORE_API_TOKEN", "aletheore-managed-audit", prompt_fn=lambda _: ""
    )
    use_hosted = bool(token)
    vectors: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        if use_hosted:
            try:
                vectors.extend(embed_texts_hosted(batch, token, repo_id=repo_id))
                continue
            except HostedEmbeddingUnavailableError as exc:
                if vectors:
                    raise
                # Nothing embedded yet, so falling back costs no consistency.
                # Printed rather than swallowed: a 402 means the plan
                # changed, which the user needs to see.
                print(f"aletheore: hosted embeddings unavailable ({exc}); using local provider")
                use_hosted = False
        vectors.extend(embed_texts(batch))

    return vectors


def _index_path(repo_path: Path) -> Path:
    return repo_path / ".aletheore" / INDEX_DIRNAME


def _chunk_hash(text: str) -> str:
    """Content hash of a chunk's embedded text.

    Keyed on the text actually sent to the model, not the file or the symbol
    name, because that is exactly what determines the vector. A symbol that
    moves down a file without changing keeps its hash and its vector; one
    whose body changes gets a new hash and is re-embedded. Renaming the
    symbol changes the header line inside `text`, so it correctly misses.
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def _reusable_vectors(index_path: Path) -> dict[str, list[float]]:
    """chunk_hash -> vector, from the existing index if there is one.

    Best-effort: any failure to read the previous index (missing, corrupt,
    or written before chunk_hash existed) just means everything is embedded
    fresh, which is the old behavior rather than an error.
    """
    if not index_path.exists():
        return {}
    try:
        table = lancedb.connect(str(index_path)).open_table(TABLE_NAME)
        return {
            row["chunk_hash"]: row["vector"]
            for row in table.to_arrow().to_pylist()
            if row.get("chunk_hash")
        }
    except Exception:  # noqa: BLE001
        return {}


def _embed_stale_by_hash(stale: list[dict], repo_id: str | None = None) -> dict[str, list[float]]:
    stale_by_hash = {chunk["chunk_hash"]: chunk["text"] for chunk in stale}
    stale_hashes = list(stale_by_hash)
    fresh_vectors = _embed_in_batches(
        [stale_by_hash[chunk_hash] for chunk_hash in stale_hashes], repo_id=repo_id
    )
    return dict(zip(stale_hashes, fresh_vectors))


def build_index(repo_path: Path, evidence: dict) -> int:
    chunks = build_chunks(evidence, repo_path)
    if not chunks:
        return 0

    for chunk in chunks:
        chunk["chunk_hash"] = _chunk_hash(chunk["text"])

    # Embedding is the whole cost of indexing - 16-80 ms per chunk against
    # microseconds for everything else - so the only optimization that
    # matters is not embedding a chunk whose text has not changed. Editing
    # one file in a 634-chunk repo re-embeds that file's chunks and reuses
    # the rest, turning a 50-second rebuild into a sub-second one.
    #
    # The table is still rewritten wholesale. That is deliberate: rewriting
    # is cheap, and it deletes rows for chunks that no longer exist for
    # free, where an upsert would leave a deleted function searchable
    # forever.
    index_path = _index_path(repo_path)
    reusable = _reusable_vectors(index_path)
    stale = [chunk for chunk in chunks if chunk["chunk_hash"] not in reusable]
    repo = _repo_id(repo_path)
    fresh = _embed_stale_by_hash(stale, repo_id=repo)
    fresh_vectors = list(fresh.values())

    # Vectors from two different embedding models cannot share an index -
    # nomic-embed-text returns 768 dimensions and text-embedding-3-small
    # returns 1536, and LanceDB rejects the mix outright with "Vector column
    # 'vector' has variable length vectors". Reproduced directly: index with
    # Ollama, lose Ollama, and the next build crashed on the fallback rather
    # than degrading.
    #
    # A provider change therefore invalidates the whole cache and re-embeds
    # from scratch, which is the correct answer anyway: the old vectors are
    # not comparable to the new ones, so keeping them would return nonsense
    # rankings even if the write succeeded.
    #
    # Keyed on dimension rather than model name because the dimension is
    # observable from what embed_texts actually returned, and embed_texts
    # chooses its provider internally. Two distinct models with matching
    # dimensions would not be caught; that costs stale-but-valid vectors,
    # not a crash or a schema error.
    #
    # This has to run even when stale is empty. If every chunk's hash
    # already matches the previous index, that's the "rebuild with no
    # changes" case the incremental-indexing benchmark above measured at
    # 0.2s - but the provider can still have changed underneath it with zero
    # code changes in between (this is precisely "index with Ollama, lose
    # Ollama": nothing edited, only the available provider). With
    # fresh_vectors empty there's nothing to compare reusable's dimension
    # against, so a one-item probe embed establishes it. Reproduced without
    # this: the table silently kept 768-dim rows, and the next search()
    # crashed on the mismatch between the table and the freshly-embedded
    # 1536-dim query vector instead of degrading.
    if reusable:
        current_dimension = (
            len(fresh_vectors[0])
            if fresh_vectors
            else len(_embed_in_batches([chunks[0]["text"]], repo_id=repo)[0])
        )
        reused_dimensions = {len(vector) for vector in reusable.values()}
        if reused_dimensions != {current_dimension}:
            reusable = {}
            stale = chunks
            fresh = _embed_stale_by_hash(stale, repo_id=repo)

    rows = [
        {**chunk, "vector": reusable.get(chunk["chunk_hash"]) or fresh[chunk["chunk_hash"]]}
        for chunk in chunks
    ]

    index_path.parent.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(index_path))
    table = db.create_table(TABLE_NAME, data=rows, mode="overwrite")
    # A full-text index beside the vectors, so search can match an exact
    # identifier as well as a meaning. Measured on Flask, full-text alone
    # beat embeddings on top-1 (80% vs 65%) because those questions used
    # Flask's own public vocabulary - url_for, blueprint, jinja - which
    # appears literally in the source. On this repo the ordering reversed
    # (64% vs 77%), because its questions are conceptual and the answer
    # lives in prose docstrings. Neither wins outright, which is the
    # argument for keeping both rather than picking one.
    #
    # Best-effort: an index that fails to build costs the exact-identifier
    # half of search, not the search itself (see _fts_candidates).
    try:
        table.create_index("text", config=FTS(), replace=True)
    except Exception:  # noqa: BLE001 - any backend failure degrades, never fails the build
        pass
    return len(rows)


def open_index(repo_path: Path):
    index_path = _index_path(repo_path)
    if not index_path.exists():
        raise IndexNotFoundError(
            f"no index found at {index_path} - run 'aletheore index {repo_path}' first"
        )
    db = lancedb.connect(str(index_path))
    return db.open_table(TABLE_NAME)


# How many chunks one file may contribute to a single result set, and how
# far past k to look when enforcing that.
#
# A large class-per-file (Flask's app.py, this repo's graph.py) has dozens of
# symbol chunks, all plausibly related to any question about that area, so it
# can take every slot and leave the answer invisible. Measured on Flask:
# app.py and sansio/app.py were 16% of chunks and took three of the four
# top-5 misses, and one query returned sansio/blueprints.py twice.
#
# Two rather than one: a file's module chunk plus its most relevant symbol is
# a genuinely useful pair, and cutting to one would discard the symbol that
# actually answers the question in favour of the overview.
MAX_CHUNKS_PER_FILE = 2
_OVERFETCH_FACTOR = 4


# Reciprocal-rank fusion constant. 60 is the value from the original RRF
# paper and the one every implementation uses; it damps the difference
# between rank 1 and rank 2 enough that neither retriever's top hit can
# dominate the other's outright.
_RRF_K = 60

# A demotion, not an exclusion, for a chunk from a declaration-only file (an
# interface, a .d.ts, a header with only prototypes) - see
# _is_declaration_only_file. Applied as a rank penalty rather than a flat
# score multiplier so a declaration-only hit competes on the same terms as
# a genuinely closer one: ranked far enough ahead of everything else, it
# still wins, and when nothing else matches at all it is still the only
# thing to return - unlike a test path, which build_chunks excludes
# outright, an interface is legitimately the answer to "where is the
# contract for X defined?".
_DECLARATION_ONLY_RANK_PENALTY = 8

# Directories holding documentation, demos or benchmarks rather than the
# library itself. Same principle as _is_test_path one step wider: nobody
# asking "where is X implemented" wants the documentation site that
# describes X, or the benchmark that times it.
#
# Measured across seven corpora: colinhacks/zod spent 28% of its top-5 slots
# outside packages/zod (its docs site and benchmark package) and google/gson
# 21% outside gson/src/main (proto, metrics, extras), while single-module
# repositories spent 0-7%. Demoting these lifts gson top-1 33.3% -> 40.0% and
# flask 68.8% -> 71.9%, with no corpus regressing on any metric.
_AUX_DIR_MARKERS = frozenset({
    "docs", "doc", "website", "site", "examples", "example", "samples",
    "sample", "demo", "demos", "playground", "benchmark", "benchmarks",
    "bench", "perf", "metrics", "fixtures", "e2e",
})

# A demotion rather than an exclusion, for the same reason interfaces are
# demoted rather than dropped: an examples/ directory is occasionally the only
# place a feature is shown, and should still be reachable when nothing else
# matches.
_AUXILIARY_RANK_PENALTY = 8



def _is_auxiliary_path(module_path: str) -> bool:
    """Whether a path is documentation, a demo or a benchmark."""
    return any(part.lower() in _AUX_DIR_MARKERS for part in module_path.split("/")[:-1])



def _rrf_fuse(vector_hits: list[dict], text_hits: list[dict]) -> list[dict]:
    """Interleave two ranked lists by reciprocal rank.

    Fusion is on rank, not score, because the two are not comparable -
    vector search returns an L2 distance and full-text returns a BM25
    relevance, on different scales with opposite polarity. Rank is the only
    thing both agree on.

    Equal weight. Weighting full-text higher was measured and helps the repo
    whose questions use its own public vocabulary while hurting the one
    whose answers live in prose, so there is no weighting that is right for
    both - and the caller does not know which kind of repo they have.
    """
    scores: dict[tuple, float] = {}
    by_key: dict[tuple, dict] = {}
    for hits in (vector_hits, text_hits):
        for rank, hit in enumerate(hits):
            key = (hit["module_path"], hit["symbol_name"], hit["start_line"])
            effective_rank = rank
            if hit.get("is_declaration_only"):
                effective_rank += _DECLARATION_ONLY_RANK_PENALTY
            if _is_auxiliary_path(hit.get("module_path") or ""):
                effective_rank += _AUXILIARY_RANK_PENALTY
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + effective_rank + 1)
            by_key[key] = hit
    return [by_key[key] for key, _ in sorted(scores.items(), key=lambda item: -item[1])]


def _fts_candidates(
    table, query_text: str, limit: int, language: str | None = None
) -> list[dict]:
    # Degrades to vector-only rather than failing: an index built before
    # full-text existed has no text_idx, and a query full of punctuation can
    # be rejected by the tokenizer. Neither is worth losing search over.
    try:
        query = table.search(query_text, query_type="fts").limit(limit)
        if language:
            # Same pre-filter as the vector side, for the same reason: a
            # minority language's fts hits would otherwise fill most of the
            # over-fetched limit with chunks that get thrown away after
            # fusion, which is exactly the situation the filter exists for.
            query = query.where(f"language = '{_escape_sql_literal(language)}'")
        return query.to_list()
    except Exception:  # noqa: BLE001
        return []


def search_index(
    repo_path: Path, query_text: str, k: int = 10, language: str | None = None
) -> list[dict]:
    table = open_index(repo_path)
    query_vector = embed_texts([query_text])[0]

    # Over-fetch, then thin by file: the chunks displaced by the per-file cap
    # have to be replaced by something, and that something is only available
    # if the search returned more than k to begin with.
    limit = k * _OVERFETCH_FACTOR
    vector_query = table.search(query_vector).limit(limit)
    if language:
        # A pre-filter, not a post-filter - restricting after ranking would
        # return fewer than k results for a language that is a minority of
        # the repo, which is exactly when the filter is worth using. Applied
        # to both retrievers, not just the vector one - see _fts_candidates.
        vector_query = vector_query.where(f"language = '{_escape_sql_literal(language)}'")
    candidates = _rrf_fuse(
        vector_query.to_list(), _fts_candidates(table, query_text, limit, language)
    )

    per_file: dict[str, int] = {}
    raw_results = []
    for candidate in candidates:
        path = candidate["module_path"]
        if per_file.get(path, 0) >= MAX_CHUNKS_PER_FILE:
            continue
        per_file[path] = per_file.get(path, 0) + 1
        raw_results.append(candidate)
        if len(raw_results) == k:
            break
    return [
        {
            "module_path": result["module_path"],
            "symbol_name": result["symbol_name"],
            "start_line": result["start_line"],
            "end_line": result["end_line"],
            "language": result["language"],
            "imports": result.get("imports") or [],
            "text": result["text"],
            "score": result.get("_distance"),
        }
        for result in raw_results
    ]
