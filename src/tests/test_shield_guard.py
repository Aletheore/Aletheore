import io
import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from aletheore.shield import deps as _deps
from aletheore.shield import guard, guard_evidence, registry
from aletheore.shield.guard import GuardReport, render_report, run_guard
from aletheore.shield.guard_evidence import Finding


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("test tried to reach the network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(registry, "DEFAULT_REGISTRY_CACHE_PATH", tmp_path / "reg-cache.json")
    monkeypatch.setattr(_deps, "_installed_import_names", lambda: set())
    monkeypatch.setattr(guard_evidence, "start_background_refresh", lambda *a, **k: False)
    monkeypatch.delenv("ALETHEORE_SHIELD_OFFLINE", raising=False)


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init(path, files=None, commit=True):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    for rel, text in (files or {}).items():
        _write(path, rel, text)
    if commit:
        _write(path, ".keep", "k\n")
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", "init")
    return path


def _write(repo, rel, text):
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class _Response:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self, *args):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _pypi_body(days_ago):
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {"releases": {"1.0": [{"upload_time_iso_8601": stamp}]}}


class _Opener:
    """names -> days since first publish (int) | None for 404 | Exception to raise."""

    def __init__(self, table=None, default=None):
        self.table = table or {}
        self.default = default
        self.urls = []

    def __call__(self, request, timeout=None, **kwargs):
        self.urls.append(request.full_url)
        key = next((k for k in self.table if k in request.full_url), None)
        value = self.table[key] if key else self.default
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise urllib.error.HTTPError(request.full_url, 404, "x", {}, io.BytesIO(b""))
        return _Response(_pypi_body(value))


def _msgs(report, severity=None):
    return [f.message for f in report.findings if severity in (None, f.severity)]


def test_fixture_diff_yields_one_missing_one_near_miss_and_no_workspace_finding(tmp_path):
    repo = _init(tmp_path / "r", {
        "requirements.txt": "requests==2.0\n",
        "package.json": json.dumps({"name": "root", "workspaces": ["packages/*"]}),
        "packages/core/package.json": json.dumps({"name": "@acme/core", "version": "1.0.0"}),
    })
    _write(repo, "requirements.txt", "requests==2.0\nzzqnopkgx==1.0\nreqeusts==1.0\n")
    _write(repo, "package.json", json.dumps({"name": "root", "workspaces": ["packages/*"],
                                             "dependencies": {"@acme/core": "1.0.0"}}))
    opener = _Opener({"reqeusts": 3}, default=None)
    report = run_guard(repo, opener=opener)
    missing = [f for f in report.findings if "does not exist" in f.message]
    near = [f for f in report.findings if "one edit from" in f.message]
    assert len(missing) == 1 and missing[0].severity == "warn" and "zzqnopkgx" in missing[0].message
    assert "PyPI" in missing[0].message
    assert len(near) == 1 and near[0].severity == "warn"
    assert "reqeusts" in near[0].message and "requests" in near[0].message
    assert len(report.findings) == 2
    assert not any("acme" in f.message for f in report.findings)
    assert not any("acme" in url for url in opener.urls)


def test_import_only_missing_name_is_worded_softly(tmp_path):
    repo = _init(tmp_path / "r", {"app.py": "x = 1\n"})
    _write(repo, "app.py", "import zzqfakepkg\n")
    report = run_guard(repo, opener=_Opener(default=None))
    found = [f for f in report.findings if "zzqfakepkg" in f.message]
    assert len(found) == 1 and found[0].severity == "info"
    assert "import names can differ from package names" in found[0].message
    assert not [f for f in report.findings if f.severity == "warn"]


def test_offline_run_reports_not_checked_never_pass(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "requests==2.0\nzzqnopkgx==1.0\nreqeusts==1.0\n")
    opener = _Opener(default=None)
    report = run_guard(repo, lookups=False, opener=opener)
    assert opener.urls == []
    assert not [f for f in report.findings if f.severity == "warn"]
    assert any("registry" in line and "2" in line for line in report.not_checked)
    near = [f for f in report.findings if "one edit from" in f.message]
    assert len(near) == 1 and near[0].severity == "info" and "registry was not checked" in near[0].message
    assert render_report(report) != ""


def test_config_and_env_switch_gate_lookups(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "requests==2.0\nzzqnopkgx==1.0\n")
    monkeypatch.setenv("ALETHEORE_SHIELD_OFFLINE", "1")
    opener = _Opener(default=None)
    report = run_guard(repo, opener=opener)
    assert opener.urls == [] and report.not_checked


def test_private_index_reports_not_checked(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "--index-url https://pypi.internal.example/simple\nrequests==2.0\nsecretlib==1.0\n")
    opener = _Opener(default=None)
    report = run_guard(repo, opener=opener)
    assert not [f for f in report.findings if f.severity == "warn"]
    assert any("1 dependency not checked" in line for line in report.not_checked)
    assert not any("secretlib" in url for url in opener.urls)


def test_recent_first_publish_is_flagged_under_30_days(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "requests==2.0\nfreshthing==1.0\n")
    report = run_guard(repo, opener=_Opener(default=5))
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.severity == "info" and "freshthing was first published 5 days ago" in finding.message


def test_old_package_is_silent(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "requests==2.0\nsolidthing==1.0\n")
    report = run_guard(repo, opener=_Opener(default=900))
    assert report.findings == ()
    assert all("scan evidence" in line for line in report.not_checked)  # only the unrelated evidence skip


def test_near_miss_alone_with_old_package_is_info_only(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "requests==2.0\nreqeusts==1.0\n")
    report = run_guard(repo, opener=_Opener(default=900))
    assert [(f.severity, "one edit from" in f.message) for f in report.findings] == [("info", True)]


def test_near_miss_that_does_not_exist_is_warn(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "requests==2.0\nreqeusts==1.0\n")
    report = run_guard(repo, opener=_Opener(default=None))
    assert sorted((f.severity, "one edit from" in f.message) for f in report.findings) == [("warn", False), ("warn", True)]


def test_plural_only_difference_does_not_alert(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "flask==2.0\n"})
    _write(repo, "requirements.txt", "flask==2.0\nrequest==1.0\n")
    report = run_guard(repo, opener=_Opener(default=900))
    assert not [f for f in report.findings if "one edit from" in f.message]


def test_lookup_failures_are_aggregated_once_per_reason(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "requests==2.0\nzzqa==1.0\nzzqb==1.0\nzzqc==1.0\n")
    report = run_guard(repo, opener=_Opener(default=urllib.error.URLError("down")))
    assert report.findings == ()
    lines = [line for line in report.not_checked if "offline or timed out" in line]
    assert len(lines) == 1 and "3" in lines[0]


def test_budget_exhaustion_stops_lookups_without_raising(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "requests==2.0\nzzqa==1.0\nzzqb==1.0\n")
    opener = _Opener(default=None)
    report = run_guard(repo, budget_s=0.0, opener=opener)
    assert opener.urls == []
    assert any("time budget reached" in line for line in report.not_checked)


def test_budget_runs_out_mid_lookups(tmp_path, monkeypatch):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    _write(repo, "requirements.txt", "requests==2.0\nzzqa==1.0\nzzqb==1.0\nzzqc==1.0\n")
    clock = {"t": 0.0}
    monkeypatch.setattr(guard, "_clock", lambda: clock["t"])
    opener = _Opener(default=None)

    def slow(request, timeout=None, **kwargs):
        clock["t"] += 1.0
        return opener(request, timeout=timeout)

    report = run_guard(repo, budget_s=2.0, opener=slow)
    assert len(opener.urls) == 2
    assert any("time budget reached" in line for line in report.not_checked)


def test_only_first_ten_names_are_looked_up(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "requests==2.0\n"})
    names = [f"zzqpkg{i:02d}" for i in range(13)]
    _write(repo, "requirements.txt", "requests==2.0\n" + "".join(f"{n}==1.0\n" for n in names))
    opener = _Opener(default=900)
    report = run_guard(repo, budget_s=60, opener=opener)
    assert len(opener.urls) == 10
    assert any("3 more names not looked up" in line for line in report.not_checked)


def test_run_guard_with_no_commits_uses_empty_base(tmp_path):
    repo = _init(tmp_path / "r", {"requirements.txt": "zzqnopkgx==1.0\n"}, commit=False)
    report = run_guard(repo, opener=_Opener(default=None))
    assert any("zzqnopkgx" in f.message and "does not exist" in f.message for f in report.findings)


def test_run_guard_never_raises(tmp_path):
    report = run_guard(tmp_path / "does-not-exist", opener=_Opener())
    assert isinstance(report, GuardReport)


def test_changed_files_include_untracked_and_modified(tmp_path):
    repo = _init(tmp_path / "r", {"a.py": "x = 1\n"})
    _write(repo, "a.py", "x = 2\n")
    _write(repo, "new dir/b.py", "y = 1\n")
    assert sorted(guard._changed_files(repo, "HEAD")) == ["a.py", "new dir/b.py"]


def test_evidence_not_checked_lines_are_passed_through(tmp_path):
    repo = _init(tmp_path / "r", {"a.py": "x = 1\n"})
    _write(repo, "a.py", "x = 2\n")
    report = run_guard(repo, opener=_Opener())
    assert any("no scan evidence" in line for line in report.not_checked)


# ---- render ----------------------------------------------------------------------------------------------------


def _f(check, sev, msg, ev="e"):
    return Finding(check, sev, msg, ev)


def test_render_is_silent_when_clean():
    assert render_report(GuardReport((), ())) == ""


def test_render_groups_by_check_with_evidence():
    report = GuardReport((_f("risks", "warn", "r"), _f("dependencies", "info", "d"), _f("affected", "warn", "a")), ())
    lines = render_report(report).splitlines()
    assert [line.split(": ")[1].split(" ")[0] for line in lines] == ["d", "a", "r"]
    assert all("(e)" in line for line in lines)


def test_render_caps_at_max_lines_with_a_more_line():
    report = GuardReport(tuple(_f("dependencies", "info", f"m{i}") for i in range(30)), ("some reason",))
    lines = render_report(report).splitlines()
    assert len(lines) == 12
    assert lines[-2].startswith("+") and "more" in lines[-2] and lines[-1].startswith("not checked:")
    assert lines[-2] == "+20 more"
    assert len(render_report(report, max_lines=5).splitlines()) == 5


def test_render_mentions_skipped_checks_in_one_line():
    report = GuardReport((_f("dependencies", "warn", "x does not exist on PyPI"),), ("r1", "r2", "r3"))
    lines = render_report(report).splitlines()
    skipped = [line for line in lines if line.startswith("not checked")]
    assert skipped == ["not checked: r1; r2 (+1 more)"]


def test_render_skipped_only_is_not_silent():
    assert render_report(GuardReport((), ("registry lookups are off",))) == "not checked: registry lookups are off"
