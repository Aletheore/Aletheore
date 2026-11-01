import shutil
import subprocess

import pytest

from app_server import frontend


pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='node unavailable')


def run_js(script):
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_status_preserving_fetch_keeps_auth_and_error_behavior():
    helpers = frontend.FETCH_HELPERS.split('async function apiPost', 1)[0]
    run_js(helpers + r'''
const assert = require('node:assert/strict');
const window = {location: {href: ''}};
let response;
async function fetch() { if (response instanceof Error) throw response; return response; }
(async () => {
  for (const status of [200, 402, 404, 500]) {
    response = {status, ok: status === 200};
    assert.equal((await apiGetWithStatus('/test')).status, status);
  }
  response = {status: 401, ok: false};
  assert.equal(await apiGetWithStatus('/test'), null);
  assert.equal(window.location.href, '/auth/logout');
  response = new Error('offline');
  assert.equal(await apiGetWithStatus('/test'), null);
  response = {status: 404, ok: false};
  assert.equal(await apiGet('/test'), null);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_graph_distinguishes_locked_missing_and_temporary_failure():
    html = frontend.WIKI_HTML
    start = html.index('async function loadGraph() {')
    end = html.index('  const data = await res.json();', start)
    graph_status_paths = html[start:end] + '\n}'
    run_js(r'''
const assert = require('node:assert/strict');
const container = {innerHTML: ''};
const document = {getElementById: () => container};
const base = '/app/test/repo';
let response;
async function apiGetWithStatus() { return response; }
function lockedFeature(title) { return title; }
''' + graph_status_paths + r'''
(async () => {
  response = {status: 402, ok: false}; await loadGraph();
  assert.match(container.innerHTML, /paid feature/);
  response = {status: 404, ok: false}; await loadGraph();
  assert.match(container.innerHTML, /No dependency graph/);
  response = {status: 503, ok: false}; await loadGraph();
  assert.match(container.innerHTML, /Try again/);
  response = null; await loadGraph();
  assert.match(container.innerHTML, /connection/);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
