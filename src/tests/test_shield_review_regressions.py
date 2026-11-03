"""Regressions from adversarial review and checked Flash comments."""
import io
import errno
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from aletheore.shield import changes, deps, guard, guard_evidence, hooks, imports, registry, setup, state, receipt, gitutil, locking
from aletheore.shield.denylist import is_denied_path
from aletheore.evidence_diff import new_cycles
from tests.air_fixtures import minimal_air_evidence


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    config = tmp_path / 'gitconfig'
    config.write_text('')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    monkeypatch.setattr(deps, '_installed_import_names', lambda: set())
    root = tmp_path / 'repo'
    root.mkdir()
    for args in [('init', '-q'), ('config', 'user.name', 'Review'), ('config', 'user.email', 'review@example.invalid'),
                 ('config', 'commit.gpgsign', 'false'), ('config', 'maintenance.auto', 'false'), ('config', 'gc.auto', '0')]:
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    (root / 'app.py').write_text('x = 1\n')
    (root / 'requirements.txt').write_text('foo==1\n')
    (root / 'package.json').write_text('{"name":"app"}')
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'base'], check=True, capture_output=True)
    return root


@pytest.mark.parametrize('name,text,manifest', [
    ('.npmrc', '@corp:registry=https://npm.corp.example/', 'package.json'),
    ('.yarnrc.yml', 'npmRegistryServer: https://npm.corp.example/', 'package.json'),
    ('pip.conf', '[global]\nindex-url=https://pypi.corp.example/simple', 'requirements.txt'),
])
def test_ignored_registry_configs_suppress_public_requests(repo, monkeypatch, name, text, manifest):
    (repo / '.gitignore').write_text(name + '\n')
    (repo / name).write_text(text)
    (repo / manifest).write_text('{"name":"app","dependencies":{"@corp/private":"1.0.0"}}'
                                 if manifest.endswith('json') else 'foo==1\ncorp-private==1\n')
    opener = MagicMock(side_effect=AssertionError('must not send a private name'))
    monkeypatch.setattr(registry, 'DEFAULT_REGISTRY_CACHE_PATH', repo / 'fake-cache.json')
    report = guard.run_guard(repo, lookups=True, opener=opener, budget_s=6)
    opener.assert_not_called()
    assert any('private' in reason for reason in report.not_checked)


def test_nested_ignored_registry_config_is_read(repo):
    (repo / '.gitignore').write_text('.npmrc\n')
    nested = repo / 'web'
    nested.mkdir()
    (nested / '.npmrc').write_text('registry=https://private.example/')
    (nested / 'package.json').write_text('{"dependencies":{"secret-private":"1.0.0"}}')
    refs, reasons = deps.added_dependencies(repo)
    assert not refs and any('private' in r.reason for r in reasons)


@pytest.mark.parametrize('url', ['https://registry.npmjs.org.private.example/', 'https://proxy.example/registry.npmjs.org/'])
def test_public_registry_match_uses_hostname(url):
    assert deps._npmrc_private('registry=' + url)


def evidence(repo, modules=()):
    ev = minimal_air_evidence()
    ev['scanned_at'] = '2000-01-01T00:00:00+00:00'
    ev['repository']['modules'] = list(modules)
    target = repo / '.aletheore/air.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(ev))
    return ev


@pytest.mark.parametrize('name', ['config.json', 'config.yml', '.env', 'settings.properties'])
def test_config_edits_request_fresh_secret_evidence(repo, monkeypatch, name):
    base = evidence(repo)
    (repo / name).write_text('new config\n')
    refresh = MagicMock(return_value=True)
    monkeypatch.setattr(guard_evidence, 'start_background_refresh', refresh)
    found, skipped = guard_evidence.evidence_checks(repo, [name], base)
    assert found == [] and any('evidence is older' in x for x in skipped)
    refresh.assert_called_once()


def test_deleted_module_invalidates_evidence(repo, monkeypatch):
    base = evidence(repo, [{'path': 'app.py', 'imports': [], 'imported_by': []}])
    (repo / 'app.py').unlink()
    refresh = MagicMock(return_value=True)
    monkeypatch.setattr(guard_evidence, 'start_background_refresh', refresh)
    _, skipped = guard_evidence.evidence_checks(repo, ['app.py'], base)
    assert any('evidence is older' in x for x in skipped)
    refresh.assert_called_once()


def test_removal_preserves_config_that_predates_setup(repo):
    path = state.shield_dir(repo, create=True) / 'config.json'
    original = b'{"registry_lookups": false}\n'
    path.write_bytes(original)
    setup.apply_setup(repo)
    setup.remove_setup(repo)
    assert path.read_bytes() == original


def test_atomic_config_failure_keeps_offline_preference(repo, monkeypatch):
    state.save_config(repo, state.ShieldConfig(False))
    monkeypatch.setattr(state.os, 'replace', MagicMock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        state.save_config(repo, state.ShieldConfig(True))
    assert not state.load_config(repo).registry_lookups
    assert not list(state.shield_dir(repo).glob('.config-*.tmp'))


@pytest.mark.parametrize('name', ['production.env', 'app.env', 'STAGING.ENV'])
def test_env_suffix_is_never_captured(name):
    assert is_denied_path(name)


@pytest.mark.parametrize('source,expected', [
    ('const code = "require(\'ghost\')"; import actual from "real-package";', {'real-package'}),
    ('const marker = "/*"; import x from "real-package"; const end = "*/";', {'real-package'}),
    ('const url = "https://example.com"; import x from "real-package";', {'real-package'}),
    ('const regex = /require("ghost")/; const x = require("real-package");', {'real-package'}),
    ('// import x from "ghost"\nimport type { X } from "real-types";', {'real-types'}),
    ('const x = `require("ghost")`; import(`real-package`);', {'real-package'}),
    ('import x = require("real-package"); export { x } from "real-export";', {'real-package', 'real-export'}),
])
def test_only_real_js_syntax_is_a_dependency(source, expected):
    assert imports.js_imports(source) == expected


def test_unrelated_setup_cfg_metadata_does_not_hide_import(repo):
    (repo / 'setup.cfg').write_text('[metadata]\nclassifiers =\n    unknownlib\n[options]\ninstall_requires =\n    foo>=1\n')
    (repo / 'app.py').write_text('import unknownlib\n')
    assert any(r.name == 'unknownlib' for r in deps.added_dependencies(repo)[0])


def test_declared_package_does_not_hide_new_extension_import(repo):
    (repo / 'app.py').write_text('import foo_extra\n')
    assert any(r.name == 'foo-extra' for r in deps.added_dependencies(repo)[0])


def test_partial_base_never_sends_unchanged_packages(repo, monkeypatch):
    # The merged tree has no manifest-count cap; a base the budget could not fully read is the partial case.
    monkeypatch.setattr(deps, '_base_unit_pins', lambda *a, **k: ({}, False))
    (repo / 'requirements.txt').write_text('foo==1\n# a comment\n')
    refs, reasons = deps.added_dependencies(repo)
    assert refs == [] and any('were not compared with the base' in r.reason for r in reasons)


def test_ambiguous_import_is_not_guessed(repo):
    (repo / 'app.py').write_text('import jwt\n')
    refs, reasons = deps.added_dependencies(repo)
    assert refs == [] and any('multiple distributions' in r.reason for r in reasons)


def test_legacy_cryptodome_namespace_maps_to_its_actual_distribution(repo):
    (repo / 'app.py').write_text('from Cryptodome.Cipher import AES\n')
    assert any(r.name == 'pycryptodomex' for r in deps.added_dependencies(repo)[0])


def test_replacement_cycle_is_new_despite_one_shared_module():
    def ev(edges):
        return {'repository': {'modules': [{'path': p, 'imports': imports} for p, imports in edges.items()]}}
    old = ev({'a.py': ['b.py'], 'b.py': ['a.py'], 'c.py': []})
    new = ev({'a.py': ['c.py'], 'b.py': [], 'c.py': ['a.py']})
    assert len(new_cycles(old, new)) == 1


def test_dedupe_is_committed_only_after_successful_delivery(monkeypatch):
    committed = []
    monkeypatch.setattr(hooks, '_work', lambda *a: hooks.Output('finding', '', lambda: committed.append(True)))
    class Broken(io.StringIO):
        def write(self, value):
            raise BrokenPipeError
    assert hooks.main(['post-edit'], stdout=Broken()) == 0
    assert committed == []
    out = io.StringIO()
    assert hooks.main(['post-edit'], stdout=out) == 0
    assert out.getvalue() == 'finding' and committed == [True]


def test_concurrent_hook_updates_keep_independent_keys(tmp_path):
    path = tmp_path / 'state.json'
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: hooks._update_state(path, {str(i): i}), range(12)))
    assert json.loads(path.read_text()) == {str(i): i for i in range(12)}


def test_concurrent_refresh_claim_has_one_winner(repo):
    marker = state.shield_dir(repo, create=True) / 'refresh.marker'
    marker.write_text(str(time.time() - 1000))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: guard_evidence._claim_marker(marker), range(16)))
    assert sum(results) == 1


def test_receipt_failure_is_explicit(repo, monkeypatch):
    monkeypatch.setattr(receipt, 'list_checkpoints', MagicMock(side_effect=RuntimeError('git unavailable')))
    built = receipt.build_receipt(repo, 'session', lookups=False)
    assert built is not None and any('receipt could not be computed' in r for r in built.report.not_checked)


def test_future_receipt_cannot_age_out_recent_receipts(repo):
    now = datetime.now(timezone.utc)
    directory = state.shield_dir(repo, create=True) / 'receipts'
    directory.mkdir()
    originals = []
    for i in range(60):
        stamp = (now - timedelta(days=1, seconds=i)).strftime('%Y%m%dT%H%M%SZ')
        path = directory / (stamp + '-old.md')
        path.write_text('recent')
        originals.append(path)
    built = receipt.Receipt('skew', now + timedelta(days=365), (), guard.GuardReport((), ()), None)
    receipt.write_receipt(repo, built, now=now)
    assert all(p.exists() for p in originals)


def test_oversized_registry_cache_is_ignored(tmp_path):
    path = tmp_path / 'cache.json'
    path.write_bytes(b' ' * (registry.MAX_CACHE_BYTES + 1))
    assert registry._read_cache(path) == {}


def test_concurrent_cache_updates_are_preserved(tmp_path):
    path = tmp_path / 'cache.json'
    now = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: registry._write_cache(path, str(i), registry.RegistryInfo(True, None, None), now), range(8)))
    assert set(registry._read_cache(path)) == {str(i) for i in range(8)}


def test_cache_keeps_latest_lookup_when_timestamps_tie(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, 'MAX_CACHE_ENTRIES', 2)
    path = tmp_path / 'cache.json'
    now = datetime.now(timezone.utc)
    for name in ['old', 'older', 'new']:
        registry._write_cache(path, name, registry.RegistryInfo(True, None, None), now)
    cached = registry._read_cache(path)
    assert len(cached) == 2 and 'new' in cached


def test_written_cache_stays_inside_readable_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, 'MAX_CACHE_BYTES', 300)
    path = tmp_path / 'cache.json'
    now = datetime.now(timezone.utc)
    for name in ['a', 'b', 'c', 'x' * 400]:
        registry._write_cache(path, name, registry.RegistryInfo(True, None, None), now)
    assert path.stat().st_size <= 300
    assert registry._read_cache(path)


def test_failed_cache_replace_leaves_no_temporary_file(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'replace', MagicMock(side_effect=OSError('disk full')))
    registry._write_cache(tmp_path / 'cache.json', 'pkg', registry.RegistryInfo(True, None, None), datetime.now(timezone.utc))
    assert not list(tmp_path.glob('*.tmp'))


def test_missing_git_reports_spawn_failure(repo, monkeypatch):
    monkeypatch.setattr(gitutil.subprocess, 'run', MagicMock(side_effect=FileNotFoundError()))
    assert 'could not be started' in gitutil.repo_state(repo).reason


def test_real_exit_124_is_not_reported_as_wrapper_timeout(repo, monkeypatch):
    monkeypatch.setattr(gitutil, 'run_git', lambda *a, **k: subprocess.CompletedProcess([], 124, b'', b''))
    assert 'time' not in gitutil.repo_state(repo).reason


def test_expired_budget_does_not_start_evidence_work(repo, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(guard, '_clock', lambda: clock[0])
    def dependencies(*a, **k):
        clock[0] = 2.0  # the dependency stage used up the budget
        return [], []
    monkeypatch.setattr(guard, 'added_dependencies', dependencies)
    work = MagicMock(side_effect=AssertionError('late evidence work'))
    monkeypatch.setattr(guard, 'evidence_checks', work)
    result = guard.run_guard(repo, budget_s=1.0)
    work.assert_not_called()
    assert any('evidence checks were not run' in r for r in result.not_checked)


def test_git_file_listing_shares_one_deadline(repo, monkeypatch):
    # The change is listed once per guard run (changes.compute); its git calls share the run's one deadline.
    elapsed = [0.0]
    timeouts = []
    def git(repo_, *args, timeout=None, **kwargs):
        timeouts.append(timeout)
        elapsed[0] += 1.0
        return gitutil.GitResult([], 0, b'', b'')
    monkeypatch.setattr(changes, 'run_git', git)
    budget = gitutil.Budget(1.5, lambda: elapsed[0])
    changes.compute(repo, 'HEAD', budget=budget)
    assert timeouts[0] == 1.5 and timeouts[1:] == [0.5]  # no base was found, so only the listing followed


def test_suggested_pytest_command_quotes_space_in_test_path(repo):
    (repo / 'pytest.ini').write_text('[pytest]\n')
    head = minimal_air_evidence()
    head['repository']['modules'] = [
        {'path': 'app.py', 'imports': [], 'imported_by': ['tests/test feature.py']},
        {'path': 'tests/test feature.py', 'imports': ['app.py'], 'imported_by': []},
    ]
    found = guard_evidence._affected_finding(repo, head, ['app.py'])
    import os
    assert ('"tests/test feature.py"' if os.name == 'nt' else "'tests/test feature.py'") in found.message


def test_git_resolution_failure_is_a_graceful_repository_diagnostic(repo, monkeypatch):
    monkeypatch.setattr(gitutil, 'git_executable', MagicMock(side_effect=FileNotFoundError('no git')))
    assert 'could not be started' in gitutil.repo_state(repo).reason


def test_old_self_imports_do_not_hide_new_mutual_cycle():
    def ev(edges):
        return {'repository': {'modules': [{'path': p, 'imports': values} for p, values in edges.items()]}}
    old = ev({'a.py': ['a.py'], 'b.py': ['b.py']})
    new = ev({'a.py': ['b.py'], 'b.py': ['a.py']})
    assert len(new_cycles(old, new)) == 1


def test_declared_suffix_does_not_hide_unrelated_new_import(repo):
    (repo / 'requirements.txt').write_text('foo-bar==1\n')
    subprocess.run(['git', '-C', str(repo), 'commit', '-qam', 'declare foo-bar'], check=True, capture_output=True)
    (repo / 'app.py').write_text('import bar\n')
    assert any(r.name == 'bar' for r in deps.added_dependencies(repo)[0])


def test_receipt_state_probe_failure_still_produces_failure_receipt(repo, monkeypatch):
    monkeypatch.setattr(state, 'session_state_path', MagicMock(side_effect=OSError('unavailable')))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    built = receipt.build_receipt(repo, 'session', lookups=False, now=now)
    assert built.session == 'unknown' and not built.session_known
    assert built.created_at == now
    assert any('receipt could not be computed' in r for r in built.report.not_checked)


@pytest.mark.parametrize('security', [None, {'secrets': None}, {'secrets': {'findings': None}}])
def test_malformed_secret_evidence_requests_a_scan(repo, monkeypatch, security):
    from aletheore import evidence as evidence_io
    head = evidence(repo)
    head['security'] = security
    monkeypatch.setattr(evidence_io, 'load_evidence', lambda *a: head)
    _, skipped = guard_evidence.evidence_checks(repo, [], None)
    assert any('unexpected shape; run aletheore scan' in x for x in skipped)


@pytest.mark.parametrize('source,expected', [
    (r'import x from "lodash\u002ecore";', {'lodash.core'}),
    (r'require("foo\u002dbar");', {'foo-bar'}),
    (r'import(`\u{40}scope\x2fpkg`);', {'@scope/pkg'}),
    (r'import x from "bad\u{110000}";', set()),
    (r'import x from "bad\nname";', set()),
])
def test_escaped_js_specifiers_are_decoded_or_safely_skipped(source, expected):
    assert imports.js_imports(source) == expected


def test_near_expired_listing_does_not_spawn_git(repo, monkeypatch):
    run = MagicMock(side_effect=AssertionError('must not spawn'))
    monkeypatch.setattr(changes, 'run_git', run)
    listed = changes.compute(repo, 'HEAD', budget=gitutil.Budget(1.0, lambda: 0.999))
    assert listed.complete is False
    run.assert_not_called()


def test_listing_timeout_has_a_budget_skip_reason(repo, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(guard, '_clock', lambda: clock[0])
    def timed_out(*a, **k):
        clock[0] = 10.0  # the listing ran into the deadline
        return gitutil.GitResult([], gitutil._TIMED_OUT)
    monkeypatch.setattr(changes, 'run_git', timed_out)
    checks = MagicMock(side_effect=AssertionError('cannot check without a listing'))
    monkeypatch.setattr(guard, 'evidence_checks', checks)
    report = guard.run_guard(repo, lookups=False, budget_s=6.0)
    assert any(r.startswith('time budget reached') for r in report.not_checked)
    checks.assert_not_called()


def test_listing_spawn_failure_is_an_environment_skip(repo, monkeypatch):
    monkeypatch.setattr(guard, 'added_dependencies', lambda *a, **k: ([], []))
    monkeypatch.setattr(changes, 'run_git', lambda *a, **k: gitutil.GitResult([], gitutil._SPAWN_FAILED, b'', b''))
    checks = MagicMock(side_effect=AssertionError('cannot check without git'))
    monkeypatch.setattr(guard, 'evidence_checks', checks)
    report = guard.run_guard(repo, lookups=False)
    assert 'git could not be started; check its installation and PATH' in report.not_checked
    checks.assert_not_called()


def test_busy_state_lock_reports_failed_save(tmp_path, monkeypatch):
    @contextmanager
    def busy(*args, **kwargs):
        yield False
    monkeypatch.setattr(hooks, 'file_lock', busy)
    debug = MagicMock()
    monkeypatch.setattr(hooks, '_debug', debug)
    assert not hooks._update_state(tmp_path / 'hook-s.json', {'edited': True})
    debug.assert_called_once()


def test_hook_state_prune_preserves_recent_and_current_sessions(tmp_path):
    now = time.time()
    current, recent, stale = [tmp_path / ('hook-' + name + '.json') for name in ['current', 'recent', 'stale']]
    for path in [current, recent, stale]:
        path.write_text('{}')
    for path in [current, stale]:
        os.utime(path, (now - 8 * 86400, now - 8 * 86400))
    hooks._prune_hook_state(current, now)
    assert current.exists() and recent.exists() and not stale.exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX lock fault injection')
def test_unlock_failure_preserves_body_exception(tmp_path, monkeypatch):
    import fcntl
    def flock(handle, operation):
        if operation == fcntl.LOCK_UN:
            raise OSError(errno.EBADF, 'unlock failure')
    monkeypatch.setattr(fcntl, 'flock', flock)
    with pytest.raises(RuntimeError, match='original'):
        with locking.file_lock(tmp_path / 'state.lock'):
            raise RuntimeError('original')


@pytest.mark.skipif(os.name == 'nt', reason='POSIX lock fault injection')
def test_noncontention_lock_failure_is_not_retried(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setattr(fcntl, 'flock', MagicMock(side_effect=OSError(errno.EBADF, 'bad descriptor')))
    pause = MagicMock(side_effect=AssertionError('must not retry'))
    monkeypatch.setattr(locking.time, 'sleep', pause)
    with pytest.raises(OSError, match='bad descriptor'):
        with locking.file_lock(tmp_path / 'state.lock'):
            pass
    pause.assert_not_called()
