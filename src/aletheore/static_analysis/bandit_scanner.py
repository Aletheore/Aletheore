import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

from aletheore.static_analysis._exclusions import excluded_dir_names, filter_findings, has_real_file

DEFAULT_BANDIT_TIMEOUT_SECONDS = 180

_SEVERITY_MAP = {"HIGH": "critical", "MEDIUM": "major", "LOW": "minor"}

# Real finding (2026-09-23, stress-testing the scanner integration against
# real repos): unfiltered against pallets/flask (a clean, well-regarded
# codebase), B101 alone was 1,054 of 1,083 total Bandit findings (97%) -
# fires on every bare `assert` statement, and Python code idiomatically
# uses `assert` constantly (96% of these were in tests/ and examples/,
# pytest's own idiom; the handful in real src/ were ordinary programmer-
# error sanity checks, e.g. `assert bool(static_host) == host_matching,
# "Invalid ..."` - not security-critical logic gated on assert). Same
# noise class PMD's own _NOISY_RULES already excludes for a different
# tool (WrongTestAnnotation/UnitTestContainsTooManyAsserts flagging test-
# authoring convention, not bugs) - same evidence-based treatment here.
_NOISY_RULES = frozenset({"B101"})

# Real false positive found live (2026-09-28, PR #858): Bandit's own B608
# check (bandit/plugins/injection_sql.py) flagged frontend.py's WIKI_HTML -
# a huge f-string building the AIRview page's HTML/JS, no SQL involved
# anywhere in this module - as "Possible SQL injection vector through
# string-based query construction." Traced to Bandit's own SIMPLE_SQL_RE:
# it pairs "update\s" with a LATER "set\s" using an UNBOUNDED `.*` under
# re.DOTALL, so it happily bridges thousands of characters of unrelated
# JS/HTML between them. The real match on that file: the word "update" in
# a code comment ("a later incremental update broke, not the first
# build"), paired ~5,700 characters later with the trailing whitespace
# after the JS identifier "nodeSet" (`const nodeSet = ...`) - "Set "
# satisfies `set\s` case-insensitively. Neither word has anything to do
# with the other or with SQL; a real SQL statement built this way (even
# split across several `+`-joined lines) never puts its own keyword pair
# more than a couple hundred characters apart.
#
# Re-verified against the real AST rather than patching Bandit's own
# installed regex (a vendored third-party tool, not this codebase) - see
# _sql_injection_is_plausible below. Only test_id "B608" goes through this
# extra check; every other Bandit rule is trusted as before.
_SQL_KEYWORD_PAIR_RE = re.compile(
    r"(select\s.{0,300}?from\s|"
    r"delete\s+from\s|"
    r"insert\s+into\s.{0,300}?values[\s(]|"
    r"update\s.{0,300}?set\s)",
    re.IGNORECASE | re.DOTALL,
)

_DB_EXECUTE_METHOD_NAMES = frozenset({"execute", "executemany"})


def _attach_ast_parents(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._sql_parent = node


def _first_str_constant_children(node: ast.JoinedStr) -> list[ast.Constant]:
    return [
        child for child in node.values
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def _binop_chain_literal_text(node: ast.BinOp) -> str:
    """Flattens a `+`/`%`-joined chain of string literals into one string,
    same shape as Bandit's own concat_string - only the literal Constant
    leaves count (a variable or call in the chain contributes nothing to
    the text a reader would see), which is enough to recognize a real
    "SELECT " + col + " FROM " + table pattern.
    """
    parts: list[str] = []

    def _walk(n: ast.AST) -> None:
        if isinstance(n, ast.BinOp):
            _walk(n.left)
            _walk(n.right)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            parts.append(n.value)

    _walk(node)
    return " ".join(parts)


def _root_binop(node: ast.BinOp) -> ast.BinOp:
    while isinstance(getattr(node, "_sql_parent", None), ast.BinOp):
        node = node._sql_parent
    return node


def _enclosing_call(node: ast.AST) -> ast.Call | None:
    """Climbs past the string-building expression `node` participates in (a
    +/%-chain, or a .format()/.replace() call) to whatever directly
    consumes the resulting value. A real db.execute(...)/executemany(...)
    call there is a strong, independent signal that this really is a SQL
    query, regardless of whether its literal text alone looks SQL-shaped -
    same "is it actually reaching a DB call" question Bandit's own
    confidence scoring asks, used here to decide whether to flag at all.
    """
    current = node
    parent = getattr(current, "_sql_parent", None)
    while isinstance(parent, ast.BinOp):
        current, parent = parent, getattr(parent, "_sql_parent", None)
    if (
        isinstance(current, ast.Constant)
        and isinstance(parent, ast.Attribute)
        and parent.attr in ("format", "replace")
    ):
        current, parent = parent, getattr(parent, "_sql_parent", None)
    return parent if isinstance(parent, ast.Call) else None


def _sql_injection_is_plausible(source: str, line: int) -> bool:
    """Re-checks a Bandit B608 finding against the real AST instead of
    trusting Bandit's own report at face value: requires the flagged
    string to actually contain a SQL-shaped keyword pair within a bounded
    distance of each other (not Bandit's own unbounded, whole-string
    reach), or to be passed directly into a real execute()/executemany()
    call - a query built entirely from interpolated pieces
    (`cursor.execute(f"SELECT * FROM {table}")`) has no literal keyword
    pair at all but is unambiguously still a real SQL-injection risk.

    Fails open (returns True, keeping Bandit's finding) whenever the
    source can't be parsed or no matching node shape is found at that
    line - an unrecognized shape is a gap in this extra check, not
    evidence Bandit's own finding is wrong.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    _attach_ast_parents(tree)

    candidates: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.lineno == line):
            continue
        parent = getattr(node, "_sql_parent", None)
        if isinstance(parent, ast.JoinedStr):
            substrings = _first_str_constant_children(parent)
            if substrings and node is substrings[0]:
                candidates.append(("".join(child.value for child in substrings), node))
        elif isinstance(parent, ast.BinOp):
            candidates.append((_binop_chain_literal_text(_root_binop(parent)), node))
        elif isinstance(parent, ast.Attribute) and parent.attr in ("format", "replace"):
            candidates.append((node.value, node))

    if not candidates:
        return True

    for text, node in candidates:
        if _SQL_KEYWORD_PAIR_RE.search(text):
            return True
        call = _enclosing_call(node)
        if call is not None and isinstance(call.func, ast.Attribute) and call.func.attr in _DB_EXECUTE_METHOD_NAMES:
            return True
    return False


def _relative_path(raw_path: str, repo_path: Path) -> str:
    # Real bug found live by this module's own tests: bandit's "filename"
    # is relative to the subprocess's cwd (repo_path, since that's what
    # this module always passes) - e.g. "./app.py" - but
    # Path(raw_path).resolve() resolves a relative path against the
    # CALLING process's cwd, not repo_path. Those are only ever the same
    # by coincidence (e.g. a test happening to run from repo_path itself);
    # for a real caller (scan_worker processing an arbitrary repo, or the
    # CLI invoked from anywhere else) they're routinely different,
    # silently producing an absolute or wrongly-rooted path instead of a
    # clean repo-relative one. Joining onto repo_path explicitly first
    # anchors the resolve() to the right base regardless of the calling
    # process's own cwd.
    # Real bug found on Windows CI (same pattern, same fix, as
    # semgrep_scanner.py's identical helper): str(Path(...)) renders with
    # the OS's native separator - a backslash-joined path on Windows -
    # while every other path in this codebase's evidence uses .as_posix()
    # specifically so paths are comparable and joinable regardless of the
    # scanning host's OS.
    try:
        return (repo_path / raw_path).resolve().relative_to(repo_path.resolve()).as_posix()
    except ValueError:
        return raw_path


def _exclude_arg(repo_path: Path) -> str | None:
    # Real bug found live: bandit's -x matches literal path prefixes, not a
    # bare directory name appearing anywhere in the path - `-x .claude`
    # left every file under .claude/worktrees/<id>/... in the results
    # (confirmed: 1,376 of them, in a real run against this repo). Only an
    # explicit `./<dir>` + `./<dir>/*` pair, relative to cwd, reliably
    # excludes a nested occurrence - confirmed the fix live the same way.
    names = excluded_dir_names(repo_path)
    if not names:
        return None
    return ",".join(f"./{name}" for name in names) + "," + ",".join(f"./{name}/*" for name in names)


def check_bandit(repo_path: Path, timeout: int = DEFAULT_BANDIT_TIMEOUT_SECONDS) -> dict:
    if not has_real_file(repo_path, "*.py"):
        return {"checked": True, "reason": None, "findings": []}

    binary = shutil.which("bandit")
    if binary is None:
        return {"checked": False, "reason": "bandit not installed", "findings": []}

    cmd = [binary, "-r", ".", "-f", "json", "-q"]
    exclude_arg = _exclude_arg(repo_path)
    if exclude_arg:
        cmd.extend(["-x", exclude_arg])

    try:
        # bandit exits non-zero (confirmed live: exit 1) whenever it finds
        # any issue - the JSON on stdout is authoritative for 0/1; any
        # other code is treated as a real failure, not partial output.
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=repo_path)
    except subprocess.TimeoutExpired:
        return {"checked": False, "reason": f"bandit timed out after {timeout}s", "findings": []}
    except OSError as exc:
        return {"checked": False, "reason": f"bandit failed to run: {exc}", "findings": []}

    if result.returncode not in (0, 1):
        return {
            "checked": False,
            "reason": f"bandit exited {result.returncode}: {(result.stderr or result.stdout)[-500:]}",
            "findings": [],
        }

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "checked": False,
            "reason": f"bandit produced unparseable output: {(result.stderr or '')[:300]}",
            "findings": [],
        }

    findings = []
    # Cache per-file source text/parse outcome across findings - a file with
    # several B608 hits (or several other findings alongside them) would
    # otherwise re-read and re-parse the same file from disk once per
    # finding.
    source_cache: dict[str, str | None] = {}
    for item in payload.get("results", []):
        test_id = item.get("test_id", "")
        if test_id in _NOISY_RULES:
            continue
        rel_path = _relative_path(item.get("filename", ""), repo_path)
        line = item.get("line_number", 0)
        if test_id == "B608":
            if rel_path not in source_cache:
                try:
                    source_cache[rel_path] = (repo_path / rel_path).read_text(
                        encoding="utf-8", errors="ignore"
                    )
                except OSError:
                    source_cache[rel_path] = None
            source = source_cache[rel_path]
            if source is not None and not _sql_injection_is_plausible(source, line):
                continue
        findings.append(
            {
                "tool": "bandit",
                "rule_id": test_id,
                "severity": _SEVERITY_MAP.get(item.get("issue_severity", ""), "minor"),
                "type": "vulnerability",
                "path": rel_path,
                "line": line,
                "message": (item.get("issue_text") or "").strip(),
            }
        )
    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
