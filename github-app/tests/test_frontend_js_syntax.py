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


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available in this environment")
@pytest.mark.parametrize("page_constant", _PAGE_CONSTANTS)
def test_embedded_script_blocks_are_valid_javascript(page_constant, tmp_path):
    html = getattr(frontend, page_constant)
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
