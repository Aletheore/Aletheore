import hashlib
import sys
from collections.abc import Callable
from pathlib import Path

import lancedb
from lancedb.index import FTS
from openai import OpenAI

from aletheore.credentials import DEFAULT_CREDENTIALS_PATH, get_api_key, has_api_key

FALLBACK_CHUNK_MAX_LINES = 200
DEFAULT_EMBEDDING_BASE_URL = "http://localhost:11434/v1"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
OPENAI_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
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
    parts = module_path.split("/")
    if any(part in {"tests", "test", "spec", "__tests__", "testing"} for part in parts):
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


def build_chunks(evidence: dict, repo_path: Path) -> list[dict]:
    chunks: list[dict] = []
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

        symbols = module["symbols"]["functions"] + module["symbols"]["classes"]
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
                        # Path and symbol list join the docstring so the chunk
                        # is reachable by what the module *contains*, not only
                        # by how its author happened to describe it.
                        "text": _truncate_for_embedding(
                            f"{module_path} (module overview)\n{head}\n\ndefines: {symbol_names}"
                        ),
                    }
                )

        for symbol in symbols:
            start_line = symbol["start_line"]
            end_line = symbol["end_line"]
            source = "\n".join(lines[start_line - 1:end_line])
            header = f"{module_path}::{symbol['name']} ({language})"
            chunks.append(
                {
                    "module_path": module_path,
                    "symbol_name": symbol["name"],
                    "start_line": start_line,
                    "end_line": end_line,
                    "language": language,
                    "imports": imports,
                    "text": _truncate_for_embedding(f"{header}\n{source}"),
                }
            )

    return chunks


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


def _embed_in_batches(texts: list[str], batch_size: int = EMBED_BATCH_SIZE) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(embed_texts(texts[start : start + batch_size]))
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
    fresh = dict(
        zip(
            (chunk["chunk_hash"] for chunk in stale),
            _embed_in_batches([chunk["text"] for chunk in stale]),
        )
    )
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
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
            by_key[key] = hit
    return [by_key[key] for key, _ in sorted(scores.items(), key=lambda item: -item[1])]


def _fts_candidates(table, query_text: str, limit: int) -> list[dict]:
    # Degrades to vector-only rather than failing: an index built before
    # full-text existed has no text_idx, and a query full of punctuation can
    # be rejected by the tokenizer. Neither is worth losing search over.
    try:
        return table.search(query_text, query_type="fts").limit(limit).to_list()
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
        # the repo, which is exactly when the filter is worth using.
        vector_query = vector_query.where(f"language = '{_escape_sql_literal(language)}'")
    candidates = _rrf_fuse(vector_query.to_list(), _fts_candidates(table, query_text, limit))
    if language:
        candidates = [c for c in candidates if c.get("language") == language]

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
