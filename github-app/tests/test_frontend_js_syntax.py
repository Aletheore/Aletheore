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
_SCRIPT_BLOCK = re.compile(r"<script>(.*?)</script>", re.DOTALL)

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
