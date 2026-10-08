from pathlib import Path

import pytest

from aletheore import file_cache
from aletheore.error_handling import map_error_handling
from aletheore.scanner.detect import detect_languages
from aletheore.secrets import _legacy_redact, find_secrets

AWS = "AKIAABCDEFGHIJKLMNOP"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("ALETHEORE_DISABLE_LOCAL_SCAN_CACHE", raising=False)
    monkeypatch.delenv("ALETHEORE_FILE_CACHE_PATH", raising=False)
    file_cache._hash_memo.clear()
    (tmp_path / ".aletheore").mkdir()
    (tmp_path / "a.py").write_text(f'KEY = "{AWS}"\n')
    (tmp_path / "b.py").write_text('class AppError(Exception):\n    pass\n\ndef f():\n    raise AppError()\n')
    (tmp_path / "c.hpp").write_text("class Boom : public std::runtime_error {};\nvoid g() { throw Boom(); }\n")
    return tmp_path


def _count_computes(monkeypatch, module, name):
    calls = []
    real = getattr(module, name)

    def spy(jobs):
        calls.append([rel for _path, rel in jobs])
        return real(jobs)

    monkeypatch.setattr(module, name, spy)
    return calls


def test_unchanged_files_are_reused_and_edited_ones_recomputed(repo, monkeypatch):
    import aletheore.secrets as secrets

    calls = _count_computes(monkeypatch, secrets, "_scan_many_for_secrets")
    first = find_secrets(repo)
    second = find_secrets(repo)
    assert first == second
    assert len(calls) == 1  # second scan was all cache hits

    (repo / "b.py").write_text('TOKEN = "ghp_' + "a" * 36 + '"\n')
    third = find_secrets(repo)
    assert calls[-1] == ["b.py"]
    assert {f["path"] for f in third["findings"]} == {"a.py", "b.py"}


def test_deleted_files_are_dropped_from_the_cache(repo):
    find_secrets(repo)
    (repo / "a.py").unlink()
    assert find_secrets(repo)["findings"] == []
    cache = file_cache.open_cache(repo)
    try:
        rows = list(cache._conn.execute("SELECT path FROM entries WHERE kind = 'secrets'"))
    finally:
        cache.close()
    assert ("a.py",) not in rows


def test_baseline_is_applied_after_the_cache_in_both_formats(repo):
    # Acceptance depends on the baseline, not the file, so a baseline edited
    # after a cached scan must still take effect: current format matches on
    # match_preview, the legacy first4...last4 format by digest.
    finding = find_secrets(repo)["findings"][0]
    assert finding["accepted"] is False

    current = [{"path": "a.py", "pattern": finding["pattern"], "match_preview": finding["match_preview"]}]
    assert find_secrets(repo, baseline=current)["findings"][0]["accepted"] is True

    legacy = [{"path": "a.py", "pattern": finding["pattern"], "match_preview": _legacy_redact(AWS)}]
    assert find_secrets(repo, baseline=legacy)["findings"][0]["accepted"] is True

    other_path = [{**legacy[0], "path": "elsewhere.py"}]
    assert find_secrets(repo, baseline=other_path)["findings"][0]["accepted"] is False
    assert "_legacy_preview_digest" not in find_secrets(repo)["findings"][0]


def test_nothing_is_read_or_written_under_the_hosted_opt_out(repo, monkeypatch):
    monkeypatch.setenv("ALETHEORE_DISABLE_LOCAL_SCAN_CACHE", "1")
    find_secrets(repo)
    map_error_handling(repo)
    detect_languages(repo)
    assert not (repo / ".aletheore" / "file-cache.db").exists()


def test_no_cache_file_outside_a_scans_aletheore_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("ALETHEORE_DISABLE_LOCAL_SCAN_CACHE", raising=False)
    (tmp_path / "a.py").write_text(f'KEY = "{AWS}"\n')
    find_secrets(tmp_path)
    assert not (tmp_path / ".aletheore").exists()


def test_a_scanner_change_invalidates_its_entries(repo, monkeypatch):
    import aletheore.secrets as secrets

    calls = _count_computes(monkeypatch, secrets, "_scan_many_for_secrets")
    find_secrets(repo)
    monkeypatch.setattr(file_cache, "code_version", lambda *a, **k: "a-different-build")
    find_secrets(repo)
    assert len(calls) == 2 and len(calls[1]) == 3


def test_cached_and_fresh_results_are_identical_for_every_stage(repo, monkeypatch):
    fresh = (find_secrets(repo), map_error_handling(repo), detect_languages(repo))
    cached = (find_secrets(repo), map_error_handling(repo), detect_languages(repo))
    monkeypatch.setenv("ALETHEORE_DISABLE_LOCAL_SCAN_CACHE", "1")
    uncached = (find_secrets(repo), map_error_handling(repo), detect_languages(repo))
    assert fresh == cached == uncached
    assert {t["name"] for t in fresh[1]["error_types"]} == {"AppError", "Boom"}


def test_explicit_cache_path_works_even_with_the_local_cache_disabled(repo, tmp_path, monkeypatch):
    # A trusted caller (e.g. a worker that owns the file, outside the checkout)
    # can still get caching.
    db = tmp_path / "trusted" / "cache.db"
    monkeypatch.setenv("ALETHEORE_DISABLE_LOCAL_SCAN_CACHE", "1")
    monkeypatch.setenv("ALETHEORE_FILE_CACHE_PATH", str(db))
    find_secrets(repo)
    assert db.exists() and not (repo / ".aletheore" / "file-cache.db").exists()


def test_a_result_is_not_stored_if_the_file_changed_while_it_was_computed(repo, monkeypatch):
    import aletheore.secrets as secrets

    real = secrets._scan_many_for_secrets

    def edit_mid_scan(jobs):
        out = real(jobs)
        (repo / "a.py").write_text("nothing here\n")
        return out

    monkeypatch.setattr(secrets, "_scan_many_for_secrets", edit_mid_scan)
    find_secrets(repo)
    monkeypatch.setattr(secrets, "_scan_many_for_secrets", real)
    file_cache._hash_memo.clear()
    assert find_secrets(repo)["findings"] == []
