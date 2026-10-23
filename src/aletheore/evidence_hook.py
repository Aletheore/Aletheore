"""Claude Code PreToolUse/PostToolUse hook that tells the agent what Aletheore already knows about a file.

When the agent is about to edit a file, or has just read one, the scan already holds facts that are not in
the file itself: who imports it, how far a change reaches, which tests cover it, who owns it, how often it
churns and what it changes with. Agents do not call the MCP tools that answer this unprompted, so the hook
hands over the answer at the moment the file is touched. It is silent when the scan has nothing worth saying.

    aletheore hook evidence                 # injected evidence block (default)
    aletheore hook evidence --mode pointer  # one line

Design points, all measured or forced by the hook contract:

- The hook runs on every file action, so it must be fast. Parsing a large `air.json` per call is not, so the
  first call distils the scan into a small per-file index (`.aletheore/hook_index.json`) keyed on the
  evidence file's mtime and size, and every later call reads only that. A scan too big to distil inside the
  hook's time budget is distilled by a detached background process and the hook stays silent until it lands.
- Once per file per session (state beside the read guard's, one file per session so parallel sessions never
  contend). Repeating the same block on every touch is exactly the noise that made generic skills cost tokens.
- Only files the scan knows. A path outside the repo, a file added since the scan, or a file with nothing to
  report produces no output at all.
- The block states facts with counts and names, never advice. It also says when the evidence predates the
  file's last edit, so a stale scan is not mistaken for truth.
- Standard library only, and it always exits 0: a hook must never break the agent's tool call.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from aletheore.git_exec import git_executable  # imports only os and shutil

_LOG = logging.getLogger(__name__)

INDEX_NAME = "hook_index.json"
_STATE_DIRNAME = "evidence_hook"
_INDEX_VERSION = 1
# Above this size the distil step is not run inline: a detached process does it and the hook stays silent
# until the index exists. Below it, building inline costs well under a second.
_INLINE_BUILD_MAX_BYTES = 25 * 1024 * 1024
_MAX_IMPORTERS_STORED = 300
_MAX_NAMED = 3
_MAX_TRANSITIVE_WALK = 5000
_GIT_LOG_COMMITS = 800
# The history read runs on the first file touch, inside the hook's time budget. A slow disk or a huge history gives
# up and the hook simply shows no history, rather than holding up the agent's tool call.
_GIT_LOG_TIMEOUT = 5
_GIT_MAX_FILES_PER_COMMIT = 25
_GIT_MIN_PAIR = 2
_TOOLS_AT_FILE_TOUCH = frozenset({"Read", "Edit", "MultiEdit", "Write"})

# Same shape as tests_for._TEST_PATH, kept local so this module imports nothing from the package at hook time.
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|e2e)(/|$)|(^|/)test_[^/]*$|_test\.[A-Za-z0-9]+$|\.(test|spec)\.[A-Za-z0-9]+$"
    # PascalCase test files and test projects (FooTests.cs, UnitTests/); case matters here, "Contests/" is not one
    r"|(?-i:[A-Za-z0-9]Tests?\.(?:cs|java|kt|kts|php|scala|swift)$|(^|/)[\w.\-]*Tests?/)",
    re.IGNORECASE,
)
_EXT = re.compile(r"\.[A-Za-z0-9]+$")


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


# --------------------------------------------------------------------------------------------------
# Index: the scan, distilled to what the hook needs
# --------------------------------------------------------------------------------------------------


def build_index(evidence: dict) -> dict:
    """The per-file facts the hook reports, from a loaded evidence dict. Pure; no I/O."""
    repository = evidence.get("repository") or {}
    modules = repository.get("modules") or []
    git = evidence.get("git") or {}
    architecture = evidence.get("architecture") or {}

    ownership = git.get("file_ownership") or {}
    hotspots = {h.get("path"): h for h in (git.get("hotspots") or []) if isinstance(h, dict)}
    cluster_of: dict[str, int] = {}
    cluster_sizes: dict[int, int] = {}
    for cluster in architecture.get("clusters") or []:
        modules_in = cluster.get("modules") or []
        cluster_sizes[cluster.get("id")] = len(modules_in)
        for path in modules_in:
            cluster_of[path] = cluster.get("id")
    violations = (architecture.get("layer_violations") or {}).get("violations") or []
    violation_files: dict[str, int] = {}
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        for key in ("from", "to"):
            value = violation.get(key)
            if isinstance(value, str):
                violation_files[value] = violation_files.get(value, 0) + 1
    unreachable = {
        item.get("path")
        for item in ((repository.get("dead_code") or {}).get("unreachable_modules") or [])
        if isinstance(item, dict)
    }

    files: dict[str, dict] = {}
    for module in modules:
        path = module.get("path")
        if not path:
            continue
        importers = list(dict.fromkeys(p for p in (module.get("imported_by") or []) if p != path))
        entry: dict[str, Any] = {
            "importers": importers[:_MAX_IMPORTERS_STORED],
            "importers_total": len(importers),
            "imports": len(set(module.get("imports") or [])),
        }
        owners = ownership.get(path) or []
        if owners:
            entry["owners"] = [
                {"name": (o.get("names") or [o.get("email", "?")])[0], "percent": round(float(o.get("percent") or 0), 2)}
                for o in owners[:2]
            ]
        hot = hotspots.get(path)
        if hot:
            entry["churn"] = hot.get("churn_count")
            entry["co_change"] = [
                {"path": p.get("path"), "n": p.get("co_occurrences")}
                for p in (hot.get("co_change_partners") or [])[:_MAX_NAMED]
            ]
        if path in cluster_of:
            entry["cluster"] = cluster_of[path]
            entry["cluster_size"] = cluster_sizes.get(cluster_of[path], 0)
        if path in violation_files:
            entry["layer_violations"] = violation_files[path]
        if path in unreachable:
            entry["unreachable"] = True
        files[path] = entry

    return {
        "version": _INDEX_VERSION,
        "scanned_at": evidence.get("scanned_at"),
        "repo_path": evidence.get("repo_path"),
        "files": files,
    }


def _fill_history_from_git(repo_path: Path, index: dict) -> None:
    """Give every file a change count and its closest co-change partners, not only the scan's few hotspots.

    The scan keeps co-change for its hotspot list alone, so most files would show no history. One bounded
    `git log` over the recent commits covers them all. Commits that touch many files (mass renames, formatting
    sweeps) say nothing about which files belong together and are skipped. Silent when git is unavailable.
    """
    files = index.get("files") or {}
    try:
        # An absolute git, as everywhere else git runs against a repository we did not write: a bare name is
        # re-resolved against PATH and a relative entry could let the repository shadow it.
        result = subprocess.run(  # noqa: S603 - fixed argv, absolute executable
            [git_executable(), "-C", str(repo_path), "log", "--no-merges", "--name-only", "--format=%x00", f"-n{_GIT_LOG_COMMITS}"],
            capture_output=True,
            text=True,
            timeout=_GIT_LOG_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if result.returncode != 0:
        return
    changes: dict[str, int] = {}
    pairs: dict[str, dict[str, int]] = {}
    for block in result.stdout.split("\x00"):
        touched = [line for line in block.split("\n") if line.strip()]
        for path in touched:
            changes[path] = changes.get(path, 0) + 1
        if not 2 <= len(touched) <= _GIT_MAX_FILES_PER_COMMIT:
            continue
        for path in touched:
            if path not in files:
                continue
            partners = pairs.setdefault(path, {})
            for other in touched:
                if other != path:
                    partners[other] = partners.get(other, 0) + 1
    for path, entry in files.items():
        if entry.get("co_change") or path not in pairs:
            continue
        # Partners must exist in the scan, or the agent is told to open a file that is gone.
        ranked = sorted(
            ((other, n) for other, n in pairs[path].items() if n >= _GIT_MIN_PAIR and other in files),
            key=lambda item: (-item[1], item[0]),
        )[:_MAX_NAMED]
        if ranked:
            entry["co_change"] = [{"path": other, "n": n} for other, n in ranked]
            entry.setdefault("churn", changes.get(path, 0))


def _evidence_stamp(aletheore_dir: Path) -> tuple[float, int] | None:
    try:
        stat = (aletheore_dir / "air.json").stat()
    except OSError:
        return None
    return stat.st_mtime, stat.st_size


def write_index(repo_path: Path) -> dict | None:
    """Distil `air.json` into the hook index and return it, or None when there is no readable evidence."""
    aletheore_dir = repo_path / ".aletheore"
    stamp = _evidence_stamp(aletheore_dir)
    if stamp is None:
        return None
    try:
        evidence = json.loads((aletheore_dir / "air.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(evidence, dict):
        return None
    index = build_index(evidence)
    _fill_history_from_git(repo_path, index)
    index["air_stamp"] = list(stamp)
    target = aletheore_dir / INDEX_NAME
    tmp = target.with_name(INDEX_NAME + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(index, separators=(",", ":")))
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
    return index


def load_index(repo_path: Path) -> dict | None:
    """The current index, rebuilt (inline for ordinary scans, in the background for huge ones) when stale."""
    aletheore_dir = repo_path / ".aletheore"
    stamp = _evidence_stamp(aletheore_dir)
    if stamp is None:
        return None
    try:
        index = json.loads((aletheore_dir / INDEX_NAME).read_text())
        if (
            isinstance(index, dict)
            and index.get("version") == _INDEX_VERSION
            and tuple(index.get("air_stamp") or ()) == stamp
        ):
            return index
    except (OSError, ValueError):
        pass
    if stamp[1] <= _INLINE_BUILD_MAX_BYTES:
        return write_index(repo_path)
    _spawn_background_build(repo_path)
    return None


def _spawn_background_build(repo_path: Path) -> None:
    try:
        subprocess.Popen(  # noqa: S603 - fixed argv, our own module
            [sys.executable, "-m", "aletheore.evidence_hook", "--build-index", str(repo_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


# --------------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------------


def _transitive_count(files: dict[str, dict], target: str) -> tuple[int, bool]:
    """Modules that reach `target` through imports, direct or not, walked on the index's importer lists."""
    seen = set(files.get(target, {}).get("importers") or [])
    queue = list(seen)
    capped = False
    while queue:
        if len(seen) >= _MAX_TRANSITIVE_WALK:
            capped = True
            break
        for importer in files.get(queue.pop(), {}).get("importers") or []:
            if importer != target and importer not in seen:
                seen.add(importer)
                queue.append(importer)
    return len(seen), capped


def _tests_among(files: dict[str, dict], target: str) -> list[str]:
    """Tests that import `target` directly, then tests named after it (test_x.py, x_test.py, x.test.ts)."""
    entry = files.get(target) or {}
    found = [p for p in (entry.get("importers") or []) if is_test_path(p)]
    stem = _EXT.sub("", PurePosixPath(target).name)
    wanted = {f"test_{stem}", f"{stem}_test", f"{stem}.test", f"{stem}.spec"}
    for path in files:
        if path not in found and is_test_path(path) and _EXT.sub("", PurePosixPath(path).name) in wanted:
            found.append(path)
    return found


_TEST_DIR_NAMES = ("tests", "test", "__tests__", "spec", "e2e")


def _fs_named_tests(repo_root: Path, target: str) -> list[str]:
    """Test files named after `target` in the test folders beside it or above it.

    The scan can leave test folders out of its module list (this repository's own scan does), so the index alone
    cannot say a test exists. Listing a handful of known test folders costs microseconds; walking the tree would not.
    """
    stem = _EXT.sub("", PurePosixPath(target).name)
    exact = {f"test_{stem}", f"{stem}_test", f"{stem}.test", f"{stem}.spec"}
    prefix = f"test_{stem}_"
    found: list[str] = []
    folder = (repo_root / target).parent
    visited = 0
    while True:
        for candidate in (folder, *(folder / name for name in _TEST_DIR_NAMES)):
            try:
                names = os.listdir(candidate)
            except OSError:
                continue
            for name in names:
                base = _EXT.sub("", name)
                if (base in exact or name.startswith(prefix)) and is_test_path(
                    (candidate / name).relative_to(repo_root).as_posix()
                ):
                    found.append((candidate / name).relative_to(repo_root).as_posix())
        visited += 1
        if folder == repo_root or visited > 8 or repo_root not in folder.parents:
            break
        folder = folder.parent
    return found


def _short(paths: list[str], limit: int = _MAX_NAMED) -> str:
    names = [PurePosixPath(p).name for p in paths[:limit]]
    extra = len(paths) - limit
    return ", ".join(names) + (f" (+{extra} more)" if extra > 0 else "")


def render(
    index: dict, rel_path: str, *, mode: str = "inject", stale_note: str | None = None, repo_root: Path | None = None
) -> str | None:
    """The text to hand the agent for `rel_path`, or None when the scan has nothing worth saying."""
    files = index.get("files") or {}
    entry = files.get(rel_path)
    if not entry:
        return None
    total = entry.get("importers_total", 0)
    tests = _tests_among(files, rel_path)
    if not tests and repo_root is not None:
        tests = _fs_named_tests(repo_root, rel_path)
    transitive, capped = _transitive_count(files, rel_path) if total else (0, False)
    if transitive < total:
        # The index keeps at most _MAX_IMPORTERS_STORED importers per file, so the walk can undercount a hub.
        transitive, capped = total, True
    churn = entry.get("churn")
    owners = entry.get("owners") or []
    co_change = [c for c in (entry.get("co_change") or []) if c.get("path")]

    notable = bool(total or churn or co_change or entry.get("layer_violations") or entry.get("unreachable"))
    if not notable:
        return None

    name = PurePosixPath(rel_path).name
    if mode == "pointer":
        bits = []
        if total:
            bits.append(f"{total} importers ({transitive}{'+' if capped else ''} transitive)")
        if tests:
            bits.append(f"{len(tests)} test file{'s' if len(tests) != 1 else ''}")
        if churn:
            bits.append(f"churn {churn}")
        if owners:
            bits.append(f"owner {owners[0]['name']}")
        line = f"Aletheore: {name} has " + ", ".join(bits) if bits else None
        if line is None:
            return None
        return line + ". Ask aletheore_get_blast_radius for detail." + (f" {stale_note}" if stale_note else "")

    lines = [f"Aletheore evidence for {rel_path}:"]
    if total:
        reach = f"{total} direct importer{'s' if total != 1 else ''}"
        reach += f", {transitive}{'+' if capped else ''} modules reached through imports"
        lines.append(f"- Reach: {reach}.")
        sample = [p for p in (entry.get("importers") or []) if not is_test_path(p)]
        if sample:
            lines.append(f"- Importers: {_short(sample)}.")
    else:
        lines.append("- Reach: nothing imports this file.")
    if len(tests) > _MAX_NAMED:
        lines.append(f"- Tests: {len(tests)} test files, e.g. {_short(tests)}.")
    elif tests:
        lines.append(f"- Tests: {_short(tests)}.")
    else:
        lines.append("- Tests: no test file imports it or is named after it.")
    if churn:
        partners = ", ".join(f"{c['path']} ({c['n']}x)" for c in co_change)
        lines.append(f"- History: {churn} changes" + (f", usually edited with {partners}." if partners else "."))
    if owners:
        top = owners[0]
        lines.append(f"- Owner: {top['name']} ({round(top['percent'] * 100)}% of commits).")
    if entry.get("layer_violations"):
        lines.append(f"- Layer rule: {entry['layer_violations']} existing violation(s) involve this file.")
    if entry.get("unreachable"):
        lines.append("- No other module imports it (may be an entry point, script or dead code).")
    if stale_note:
        lines.append(f"- {stale_note}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------
# Hook plumbing
# --------------------------------------------------------------------------------------------------


def _session_state_path(repo_path: Path, session_id: str) -> Path | None:
    if not session_id or "/" in session_id or "\\" in session_id or session_id in (".", ".."):
        return None
    if not (repo_path / ".aletheore").is_dir():
        return None
    return repo_path / ".aletheore" / _STATE_DIRNAME / f"{session_id}.shown"


def _already_shown(state_path: Path, rel_path: str) -> bool:
    try:
        return rel_path in state_path.read_text().splitlines()
    except OSError:
        return False


def _mark_shown(state_path: Path, rel_path: str) -> None:
    # One path per line, appended: Claude Code can run tool calls in parallel inside one session, and a
    # read-modify-write of a single list would let one call's entry overwrite another's. An append of a short
    # line is a single write, so concurrent hook calls cannot lose each other's paths.
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with state_path.open("a", encoding="utf-8") as handle:
            handle.write(rel_path + "\n")
    except OSError:
        pass


def _find_repo_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".aletheore" / "air.json").is_file():
            return candidate
    return None


def _relative(repo_root: Path, file_path: str) -> str | None:
    try:
        return Path(file_path).resolve().relative_to(repo_root.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _staleness_note(index: dict, repo_root: Path, rel_path: str) -> str | None:
    """Tell the agent when the file changed after the scan, so the importer list is not read as current."""
    scanned_at = index.get("scanned_at")
    if not isinstance(scanned_at, str):
        return None
    try:
        from datetime import datetime

        scanned = datetime.fromisoformat(scanned_at).timestamp()
        modified = (repo_root / rel_path).stat().st_mtime
    except (ValueError, OSError):
        return None
    if modified > scanned + 1:
        return "This file changed after the last scan, so importers and symbols may be out of date (rescan to refresh)."
    return None


def evidence_for_payload(payload: dict[str, Any], *, mode: str = "inject") -> dict[str, Any] | None:
    """The hook JSON to print for a PreToolUse/PostToolUse payload, or None to stay silent."""
    tool = payload.get("tool_name")
    event = payload.get("hook_event_name")
    tool_input = payload.get("tool_input")
    cwd = payload.get("cwd")
    session_id = payload.get("session_id")
    if tool not in _TOOLS_AT_FILE_TOUCH or not isinstance(tool_input, dict) or not isinstance(cwd, str):
        return None
    # Edits get the evidence before they happen (that is when impact matters); reads get it afterwards, once the
    # file has been shown. Anything else the harness may route here is ignored.
    if event == "PreToolUse" and tool == "Read":
        return None
    if event == "PostToolUse" and tool != "Read":
        return None
    if event not in ("PreToolUse", "PostToolUse"):
        return None
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return None
    repo_root = _find_repo_root(Path(cwd))
    if repo_root is None:
        return None
    rel_path = _relative(repo_root, file_path)
    if rel_path is None:
        return None
    index = load_index(repo_root)
    if index is None:
        return None
    if rel_path not in (index.get("files") or {}):
        return None

    state_path = _session_state_path(repo_root, session_id) if isinstance(session_id, str) else None
    if state_path is not None and _already_shown(state_path, rel_path):
        return None
    text = render(
        index, rel_path, mode=mode, stale_note=_staleness_note(index, repo_root, rel_path), repo_root=repo_root
    )
    if text is None:
        return None
    if state_path is not None:
        _mark_shown(state_path, rel_path)
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def main(argv: list[str] | None = None, stdin=None, stdout=None) -> int:
    """Entry point for `aletheore hook evidence`. Always exits 0 and prints nothing unless it has evidence."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args[:1] == ["--build-index"] and len(args) > 1:
            write_index(Path(args[1]))
            return 0
        mode = "inject"
        for position, arg in enumerate(args):
            if arg == "--mode" and position + 1 < len(args):
                mode = args[position + 1]
            elif arg.startswith("--mode="):
                mode = arg.split("=", 1)[1]
        if mode not in ("inject", "pointer"):
            mode = "inject"
        payload = json.loads((stdin or sys.stdin).read() or "{}")
        if not isinstance(payload, dict):
            return 0
        out = evidence_for_payload(payload, mode=mode)
        if out:
            (stdout or sys.stdout).write(json.dumps(out))
    except BaseException:  # a hook must never break the tool call; keep the reason for debugging
        _LOG.debug("evidence hook failed; staying silent", exc_info=True)
        if os.environ.get("ALETHEORE_HOOK_DEBUG"):
            # No logging handler exists in a hook process, so the debug record above goes nowhere. Stderr is not
            # part of the JSON on stdout, so it is safe to use when someone is asking why the hook said nothing.
            import traceback

            traceback.print_exc(file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
