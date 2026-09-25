import re
import shutil
import subprocess

import pytest

from app_server import frontend

# Every dashboard page is a Python string constant with an embedded <script>
# block, built out of ordinary '...' + '...' JavaScript string concatenation
# - which means an apostrophe inside that JS text (a contraction, a
# possessive) needs escaping at the JS level, not the Python one, since
# these constants are plain triple-quoted Python strings, not raw strings.
# Get that escaping wrong (one backslash instead of two, or vice versa) and
# Python happily accepts it - the bug only shows up as broken JavaScript in
# a real browser. Real bug, caught this way once already: see the commit
# that added this test.
_SCRIPT_BLOCK = re.compile(r"<script>(.*?)</script>", re.DOTALL | re.IGNORECASE)

_PAGE_CONSTANTS = [
    name
    for name in dir(frontend)
    if name.endswith("_HTML") and isinstance(getattr(frontend, name), str)
]

# _settings_html() and _overview_html() are built as zero-argument,
# lru_cache'd functions instead of module-level constants (both defer
# get_settings() to the first real request rather than Python import time,
# for the real inline Paddle checkout each needs - see either one's own
# docstring), so neither matches the isinstance(..., str) filter above and
# both would otherwise silently fall out of this test's coverage entirely.
# Named explicitly so their <script> blocks (including buyCredit()) keep
# getting the same JS syntax check as every other dashboard page.
_PAGE_CONSTANTS = _PAGE_CONSTANTS + ["_settings_html", "_overview_html"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available in this environment")
@pytest.mark.parametrize("page_constant", _PAGE_CONSTANTS)
def test_embedded_script_blocks_are_valid_javascript(page_constant, tmp_path):
    page = getattr(frontend, page_constant)
    html = page() if callable(page) else page
    scripts = _SCRIPT_BLOCK.findall(html)
    if not scripts:
        pytest.skip(f"{page_constant} has no <script> block")

    for i, script in enumerate(scripts):
        js_file = tmp_path / f"{page_constant}_{i}.js"
        js_file.write_text(script)
        result = subprocess.run(
            ["node", "--check", str(js_file)], capture_output=True, text=True
        )
        assert result.returncode == 0, (
            f"{page_constant}'s script block {i} is not valid JavaScript:\n{result.stderr}"
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available in this environment")
def test_credits_page_script_is_valid_javascript(tmp_path):
    # Built by a function of the installation id, so it is not picked up by the
    # *_HTML constant sweep above.
    html = frontend._credits_page(123)
    for i, script in enumerate(_SCRIPT_BLOCK.findall(html)):
        js_file = tmp_path / f"credits_{i}.js"
        js_file.write_text(script)
        result = subprocess.run(["node", "--check", str(js_file)], capture_output=True, text=True)
        assert result.returncode == 0, f"credits page script block {i} is not valid JavaScript:\n{result.stderr}"


def test_wiki_markdown_escapes_before_promoting_tags():
    """AIRview file pages are model-written from repository content, so the
    renderer must escape first and only then promote markdown. If those steps
    were ever reordered, a repo could smuggle live HTML into the dashboard
    through the model's output."""
    js = frontend.FETCH_HELPERS
    body = js[js.index("function renderWikiMarkdown") :]
    body = body[: body.index("\nfunction ", 1)] if "\nfunction " in body[1:] else body
    escape_at = body.index("escapeHtml(String(src")
    promote_at = body.index("wiki-md-h")
    assert escape_at < promote_at, "markdown promoted before escaping"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_wiki_markdown_renders_untrusted_html_inert():
    js = frontend.FETCH_HELPERS
    start = js.index("function renderWikiMarkdown")
    end = js.index("\n}", js.index("return out.join")) + 2
    harness = (
        js[start:end]
        + "\nfunction escapeHtml(s){return String(s).replace(/[&<>\"']/g,"
        "function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"
        "\"'\":'&#39;'}[c];});}\n"
        "const out = renderWikiMarkdown('## H\\n<img src=x onerror=alert(1)>');\n"
        "if (/<img/i.test(out)) { throw new Error('live HTML survived: ' + out); }\n"
        "if (!out.includes('&lt;img')) { throw new Error('not escaped: ' + out); }\n"
        "if (!out.includes('wiki-md-h')) { throw new Error('heading not promoted'); }\n"
    )
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_wiki_banner_does_not_claim_a_separate_fast_model_for_incremental_updates():
    """model_tiers.resolve_model() has routed both the full AIRview build and
    every incremental update to the same model (Luna, whenever OPENAI_API_KEY
    is configured) since the 2026-08-09 routing change - see
    scan_worker/model_tiers.py's module docstring. The banner used to promise
    a cheap/fast model for updates, which stopped being true that day."""
    assert "kept current by a fast one" not in frontend.WIKI_HTML
    assert "frontier model" in frontend.WIKI_HTML


def _extract_js_function(source, name):
    # Balanced-brace extraction, not just js.index("\n}", ...) (that only
    # works for a function with no nested {} blocks of its own) - buySeat/
    # removeSeat/generateToken all have if/else and object-literal braces
    # nested inside them.
    marker = f"function {name}("
    start = source.index(marker)
    if source[max(0, start - 6) : start] == "async ":
        start -= 6
    open_brace = source.index("{", start)
    depth = 0
    for i in range(open_brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces extracting {name}")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    "name,path,method",
    [
        ("buySeat", "/seats/buy", "POST"),
        ("removeSeat", "/seats/remove", "POST"),
    ],
)
def test_seat_billing_button_disabled_for_the_whole_request_and_reenabled_on_failure(name, path, method):
    # Real gap found via audit: buySeat/removeSeat had no double-click
    # guard at all - a second click landing before the first response came
    # back fired a second, genuinely separate POST to a real-money billing
    # endpoint. The backend's per-installation lock only serializes two
    # such requests against each other, it does not collapse them into
    # one purchase, so a customer double-clicking "Buy extra seat" could
    # be billed for two seats from what looked like one click. This
    # actually executes the extracted function under node (not just a
    # string search for "disabled = true" somewhere in the file) against a
    # controllable fake fetch, proving both that the button is disabled
    # BEFORE the network call resolves (the only time window a double-click
    # actually matters) and that a failed request re-enables it rather than
    # leaving the button permanently stuck.
    js = frontend._settings_html()
    fn = _extract_js_function(js, name)
    harness = (
        fn
        + f"""
const calls = [];
const btn = {{ disabled: false }};
const status = {{ textContent: '', style: {{}} }};
const adminBase = '';
function loadSettings() {{ calls.push('loadSettings'); }}
global.document = {{ getElementById: function (id) {{ return status; }} }};
global.fetch = function (url, opts) {{
  calls.push([url, (opts || {{}}).method]);
  return Promise.resolve({{ ok: false, json: function () {{ return Promise.resolve({{ detail: 'nope' }}); }} }});
}};

const p = {name}(btn);
// Synchronous up to the first await - btn.disabled must already be true
// here, before the fetch promise has had any chance to settle.
if (btn.disabled !== true) {{ throw new Error('button not disabled before the network call'); }}

p.then(function () {{
  if (btn.disabled !== false) {{ throw new Error('button left disabled after a failed request'); }}
  if (calls.length !== 1) {{ throw new Error('expected exactly one fetch call, got ' + JSON.stringify(calls)); }}
  if (calls[0][0] !== adminBase + '{path}') {{ throw new Error('wrong URL: ' + calls[0][0]); }}
  if (calls[0][1] !== '{method}') {{ throw new Error('wrong method: ' + calls[0][1]); }}
  if (calls.indexOf('loadSettings') !== -1) {{ throw new Error('loadSettings should not run on failure'); }}
}}).catch(function (e) {{ console.error(e); process.exitCode = 1; }});
"""
    )
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", ["buySeat", "removeSeat"])
def test_seat_billing_button_reenables_after_a_network_failure_not_just_an_http_error(name):
    # Real gap found by Flash Review on the double-click-guard change
    # itself (#741): re-enabling only on the explicit else (HTTP error)
    # branch and the success path left the button stuck disabled forever
    # if fetch() itself REJECTED (network drop, timeout, DNS failure) -
    # the function exits via an unhandled promise rejection before res/data
    # ever exist, and no code path was left to run btn.disabled = false.
    # For a real-money action, that's a permanently stuck button with no
    # recovery short of a full page reload. try/finally must re-enable on
    # every exit, not just the two branches reachable when fetch() itself
    # succeeds.
    js = frontend._settings_html()
    fn = _extract_js_function(js, name)
    harness = (
        fn
        + f"""
const status = {{ textContent: '', style: {{}} }};
const adminBase = '';
global.document = {{ getElementById: function (id) {{ return status; }} }};
function loadSettings() {{ throw new Error('loadSettings must not run on a network failure'); }}
global.fetch = function (url, opts) {{
  return Promise.reject(new Error('network error'));
}};

const btn = {{ disabled: false }};
const p = {name}(btn);
if (btn.disabled !== true) {{ throw new Error('button not disabled before the network call'); }}

p.then(function () {{
  throw new Error('promise should have rejected, not resolved');
}}, function (err) {{
  if (btn.disabled !== false) {{ throw new Error('button left disabled after a rejected fetch'); }}
}}).catch(function (e) {{ console.error(e); process.exitCode = 1; }});
"""
    )
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_generate_token_button_disabled_for_the_whole_request_and_reenabled_on_failure():
    # Same real gap and fix as buySeat/removeSeat, in the "API tokens"
    # settings block (generateToken) - lower stakes (no money changes
    # hands), but the same double-click-fires-twice shape, fixed the same
    # way for consistency with every other button-triggered action on this
    # page.
    js = frontend._settings_html()
    fn = _extract_js_function(js, "generateToken")
    harness = (
        fn
        + """
const calls = [];
const btn = { disabled: false };
const input = { value: 'CI pipeline', focus: function () {} };
const out = { innerHTML: '' };
const adminBase = '';
function escapeHtml(s) { return s; }
function refreshTokenList() { calls.push('refreshTokenList'); }
global.document = {
  getElementById: function (id) { return id === 'new-token-label' ? input : out; },
};
global.fetch = function (url, opts) {
  calls.push([url, (opts || {}).method]);
  return Promise.resolve({ ok: false, json: function () { return Promise.resolve({}); } });
};

const p = generateToken(btn);
if (btn.disabled !== true) { throw new Error('button not disabled before the network call'); }

p.then(function () {
  if (btn.disabled !== false) { throw new Error('button left disabled after a failed request'); }
  if (calls.indexOf('refreshTokenList') !== -1) { throw new Error('refreshTokenList should not run on failure'); }
}).catch(function (e) { console.error(e); process.exitCode = 1; });
"""
    )
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_generate_token_button_reenables_after_a_network_failure_not_just_an_http_error():
    # Same real gap and fix as buySeat/removeSeat's own network-failure
    # test above, in generateToken.
    js = frontend._settings_html()
    fn = _extract_js_function(js, "generateToken")
    harness = (
        fn
        + """
const input = { value: 'CI pipeline', focus: function () {} };
const out = { innerHTML: '' };
const adminBase = '';
global.document = {
  getElementById: function (id) { return id === 'new-token-label' ? input : out; },
};
function refreshTokenList() { throw new Error('refreshTokenList must not run on a network failure'); }
global.fetch = function (url, opts) {
  return Promise.reject(new Error('network error'));
};

const btn = { disabled: false };
const p = generateToken(btn);
if (btn.disabled !== true) { throw new Error('button not disabled before the network call'); }

p.then(function () {
  throw new Error('promise should have rejected, not resolved');
}, function (err) {
  if (btn.disabled !== false) { throw new Error('button left disabled after a rejected fetch'); }
}).catch(function (e) { console.error(e); process.exitCode = 1; });
"""
    )
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_billing_actions_are_truly_shared_not_duplicated_per_page():
    # Settings and Overview both need buySeat/removeSeat/openBillingPortal/
    # buyCredit - real money-handling code that was duplicated once already
    # (a second, separately-maintained copy for the standalone /credits
    # page's own installation-scoped API shape). Asserting the exact same
    # BILLING_ACTIONS_JS text appears in both pages (not just "a function
    # with this name exists in both", which a second hand-written copy
    # would also satisfy) is what actually proves this isn't duplicated
    # again the same way.
    settings_js = frontend._settings_html()
    overview_js = frontend._overview_html()
    assert frontend.BILLING_ACTIONS_JS in settings_js
    assert frontend.BILLING_ACTIONS_JS in overview_js
    for name in ("buySeat", "removeSeat", "openBillingPortal", "buyCredit"):
        # Each function's own source should appear exactly once per page
        # (from BILLING_ACTIONS_JS) - a stray second definition would mean
        # the extraction didn't fully replace the old inline copy.
        assert settings_js.count(f"async function {name}(") == 1
        assert overview_js.count(f"async function {name}(") == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_overview_usage_stepper_wires_to_the_shared_buy_credit_function():
    # Real gap this guards against: loadUsage() builds the stepper/button
    # markup as an HTML string, and it's easy for that string's
    # onclick="buyCredit(this)" to silently drift from BILLING_ACTIONS_JS's
    # actual function name (or never get wired at all) without any test
    # catching it, since the JS-syntax test above only checks the script
    # parses, not that the two pieces reference each other correctly.
    js = frontend._overview_html()
    fn = _extract_js_function(js, "loadUsage")
    assert "onclick=\\'buyCredit(this)\\'" not in fn  # wrong quoting would silently no-op the button
    assert "buyCredit(this)" in fn
    assert "buySeat(this)" in fn
    assert "openBillingPortal()" in fn


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_parse_docs_markdown_finds_every_symbol_across_both_sections():
    # Real bug found while building this: a first version split the
    # markdown on a regex matching "#" or "##" headers (##?\s) to separate
    # ## Classes/## Functions sections, then re-tested each resulting
    # block for a leading "### " symbol header - but ##? never matches
    # "###", so every ### line stayed buried inside its enclosing section
    # block and zero symbols were ever extracted (89/89 missing against
    # this repo's own real db.py). A line-by-line scan tracking the
    # current section's kind, rather than a nested split-by-header-level
    # regex, is what actually works - this test pins that down.
    js = frontend.DOCS_HTML
    fn = _extract_js_function(js, "parseDocsMarkdown")
    markdown = (
        "# a/module.py\n\n"
        "## Classes\n\n"
        "### `Foo`\n\n"
        "*Undocumented - no docstring found.*\n\n"
        "`a/module.py:10`\n\n"
        "## Functions\n\n"
        "### `bar(x: int) -> str`\n\n"
        "Converts x to a string.\n\n"
        "*(AI-polished from the original docstring)*\n\n"
        "`a/module.py:25`\n"
    )
    harness = fn + f"""
const symbols = parseDocsMarkdown({markdown!r});
if (symbols.length !== 2) throw new Error('expected 2 symbols, got ' + symbols.length + ': ' + JSON.stringify(symbols));
if (symbols[0].kind !== 'class' || symbols[0].name !== 'Foo' || !symbols[0].isUndocumented) {{
  throw new Error('class symbol wrong: ' + JSON.stringify(symbols[0]));
}}
if (symbols[1].kind !== 'function' || symbols[1].name !== 'bar' || !symbols[1].isPolished) {{
  throw new Error('function symbol wrong: ' + JSON.stringify(symbols[1]));
}}
if (symbols[1].citation !== 'a/module.py:25') throw new Error('citation wrong: ' + symbols[1].citation);
if (symbols[1].description.indexOf('Converts x to a string') === -1) throw new Error('description missing: ' + symbols[1].description);
console.log('ok');
"""
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
