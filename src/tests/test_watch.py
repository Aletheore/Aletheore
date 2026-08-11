import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from aletheore.watch import _DebouncedHandler, _is_relevant, rebuild, watch

# Patched at their source modules, not on aletheore.watch: every heavy import
# in watch.py is function-local so that `aletheore --help` does not drag in
# lancedb (see test_cli.py's import-weight guard). A function-local import
# resolves the patched attribute at call time, so this works and the module
# stays cheap to import.


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.mark.parametrize(
    "path, relevant",
    [
        ("app.py", True),
        ("pkg/mod.ts", True),
        ("main.go", True),
        # The loop-breaker: a scan writes air.json, air.toon, scan-cache.json,
        # a history snapshot and the LanceDB index in here. Without this
        # exclusion the rebuild's own output retriggers the watcher forever.
        (".aletheore/air.json", False),
        (".aletheore/index.lancedb/chunks.lance/data.bin", False),
        (".aletheore/history/2026-01-01.json", False),
        ("node_modules/x/index.js", False),
        (".git/COMMIT_EDITMSG", False),
        ("README.md", False),
        ("image.png", False),
    ],
)
def test_only_source_files_outside_aletheore_are_relevant(tmp_path, path, relevant):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    assert _is_relevant(tmp_path, target) is relevant


def test_a_burst_of_saves_becomes_one_rebuild(tmp_path):
    """Format-on-save across a package, a branch checkout, or a rebase is
    hundreds of events within a second. Each must not be its own rebuild."""
    handler = _DebouncedHandler(tmp_path)

    for index in range(50):
        target = tmp_path / f"f{index}.py"
        target.write_text("x")
        handler.on_any_event(type("E", (), {"is_directory": False, "src_path": str(target)})())

    # Still settling: nothing handed over yet.
    assert handler.take_settled_batch(debounce_seconds=5.0) is None

    batch = handler.take_settled_batch(debounce_seconds=0.0)
    assert batch is not None and len(batch) == 50
    # Drained, so the next cycle does not rebuild the same batch again.
    assert handler.take_settled_batch(debounce_seconds=0.0) is None


def test_a_move_records_both_ends(tmp_path):
    """One file left a path and another arrived at one; both matter."""
    handler = _DebouncedHandler(tmp_path)
    (tmp_path / "old.py").write_text("x")
    (tmp_path / "new.py").write_text("x")

    handler.on_any_event(
        type("E", (), {
            "is_directory": False,
            "src_path": str(tmp_path / "old.py"),
            "dest_path": str(tmp_path / "new.py"),
        })()
    )

    batch = handler.take_settled_batch(debounce_seconds=0.0)
    assert {path.name for path in batch} == {"old.py", "new.py"}


def test_rebuild_refreshes_evidence_and_skips_the_slow_checks(tmp_path):
    """Vulnerability, license and history checks are network- and
    history-bound, take seconds to minutes, and do not change because a
    function body was edited."""
    repo = _git_repo(tmp_path)
    (repo / "app.py").write_text("def f():\n    return 1\n\ndef added_later():\n    return 2\n")

    with patch("aletheore.evidence.scan_repository", wraps=None) as scan:
        scan.return_value = {"repository": {"modules": []}}
        with patch("aletheore.evidence.write_evidence"):
            rebuild(repo, lambda _message: None)

    assert scan.call_args.kwargs == {
        "check_vulnerabilities": False,
        "check_licenses": False,
        "scan_git_history": False,
    }


def test_rebuild_does_not_build_a_first_index_unasked(tmp_path):
    """Building a first index embeds every chunk - it needs a provider and
    real time. Starting that because someone edited a file would surprise."""
    repo = _git_repo(tmp_path)

    with patch("aletheore.search_index.build_index") as build:
        rebuild(repo, lambda _message: None)

    build.assert_not_called()


def test_rebuild_refreshes_an_index_that_already_exists(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".aletheore" / "index.lancedb").mkdir(parents=True)

    with patch("aletheore.search_index.build_index", return_value=7) as build:
        messages: list[str] = []
        rebuild(repo, messages.append)

    build.assert_called_once()
    assert "index updated (7 chunks)" in messages


def test_an_unreachable_embedder_does_not_end_the_watch(tmp_path):
    """Evidence is already current, which is most of the value, and the next
    edit retries."""
    repo = _git_repo(tmp_path)
    (repo / ".aletheore" / "index.lancedb").mkdir(parents=True)

    with patch("aletheore.search_index.build_index", side_effect=RuntimeError("ollama down")):
        messages: list[str] = []
        rebuild(repo, messages.append)

    assert any("index not updated" in message for message in messages)
    assert any("evidence updated" in message for message in messages)


def test_watch_rebuilds_on_a_real_edit_and_does_not_retrigger_itself(tmp_path):
    """The end-to-end property that matters: a rebuild writes into
    .aletheore/, and those writes must not start another rebuild."""
    repo = _git_repo(tmp_path)
    messages: list[str] = []
    stop = threading.Event()
    thread = threading.Thread(
        target=watch, args=(repo, messages.append),
        kwargs={"debounce_seconds": 0.3, "stop": stop}, daemon=True,
    )
    thread.start()
    time.sleep(1.0)

    (repo / "app.py").write_text("def f():\n    return 2\n\ndef brand_new():\n    return 3\n")
    time.sleep(4.0)
    settled = len(messages)
    time.sleep(3.0)

    stop.set()
    thread.join(timeout=6)

    assert any("evidence updated" in message for message in messages)
    assert len(messages) == settled, f"watcher retriggered on its own writes: {messages[settled:]}"


def test_a_failing_scan_does_not_end_the_session(tmp_path):
    """A half-written file or a syntax error mid-keystroke should not end a
    session that the next save would fix."""
    repo = _git_repo(tmp_path)
    messages: list[str] = []
    stop = threading.Event()

    with patch("aletheore.evidence.scan_repository", side_effect=RuntimeError("bad parse")):
        thread = threading.Thread(
            target=watch, args=(repo, messages.append),
            kwargs={"debounce_seconds": 0.3, "stop": stop}, daemon=True,
        )
        thread.start()
        time.sleep(0.8)
        (repo / "app.py").write_text("def broken(:\n")
        time.sleep(3.0)
        alive = thread.is_alive()
        stop.set()
        thread.join(timeout=6)

    assert alive, "watch exited on a failed rebuild"
    assert any("rebuild failed" in message for message in messages)
