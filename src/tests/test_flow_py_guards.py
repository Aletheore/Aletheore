"""Python guards: a call that never returns ends the branch, and an anchored plain-character regex check proves the value harmless."""
import pytest

from aletheore.flow.discover import discover_repo

HEAD = "import os, re, sys\nfrom flask import Flask, request, abort\napp = Flask(__name__)\nALLOWED = ['a', 'b']\n@app.route('/r')\ndef r():\n    v = request.args['q']\n"
TAIL = "    os.system('echo ' + v)\n    return 'ok'\n"


def _count(tmp_path, guard):
    (tmp_path / "app.py").write_text(HEAD + guard + TAIL)
    return len(discover_repo(tmp_path))


@pytest.mark.parametrize("guard", [
    "    if v not in ALLOWED:\n        abort(400)\n",
    "    if not v.isdigit():\n        abort(400)\n",
    "    if v not in ALLOWED:\n        sys.exit(1)\n",
    "    if not re.fullmatch(r'[a-z0-9]+', v):\n        abort(400)\n",
    "    if not re.match(r'^[a-z0-9]+$', v):\n        abort(400)\n",
])
def test_guard_that_aborts_keeps_the_sink_quiet(tmp_path, guard):
    assert _count(tmp_path, guard) == 0


@pytest.mark.parametrize("guard", [
    "    if v not in ALLOWED:\n        print('bad')\n",                                  # logs and carries on
    "    if not re.fullmatch(r'[a-z .;]+', v):\n        abort(400)\n",                   # admits spaces and ;
    "    if not re.search(r'[a-z0-9]+', v):\n        abort(400)\n",                      # unanchored
    "    if not re.match(r'[a-z0-9]+', v):\n        abort(400)\n",                       # anchored at the start only
    "    if not re.fullmatch(r'.*', v):\n        abort(400)\n",
])
def test_guard_that_does_not_prove_anything_still_reports(tmp_path, guard):
    assert _count(tmp_path, guard) == 1
