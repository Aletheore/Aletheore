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


HELPERS = "import os, re\nfrom flask import Flask, request, abort\napp = Flask(__name__)\n{helper}\n@app.route('/r')\ndef r():\n    v = request.args['q']\n{use}    os.system('echo ' + v)\n    return 'ok'\n"


def _helper_count(tmp_path, helper, use):
    (tmp_path / "app.py").write_text(HELPERS.format(helper=helper, use=use))
    return len(discover_repo(tmp_path))


@pytest.mark.parametrize("helper,use", [
    ("def ok(v):\n    return bool(re.fullmatch(r'[a-z0-9]+', v))", "    if not ok(v):\n        abort(400)\n"),
    ("def ok(v):\n    return re.fullmatch(r'[a-z0-9]+', v) is not None", "    if not ok(v):\n        abort(400)\n"),
    ("def validate(v):\n    if not re.fullmatch(r'[a-z0-9]+', v):\n        raise ValueError('bad')", "    validate(v)\n"),
    ("def validate(v):\n    assert v.isdigit()", "    validate(v)\n"),
])
def test_validator_function_proves_its_argument(tmp_path, helper, use):
    assert _helper_count(tmp_path, helper, use) == 0


@pytest.mark.parametrize("helper,use", [
    ("def ok(v):\n    return len(v) < 10", "    if not ok(v):\n        abort(400)\n"),
    ("def ok(v):\n    return bool(re.fullmatch(r'[a-z ;]+', v))", "    if not ok(v):\n        abort(400)\n"),
    ("def validate(v):\n    if len(v) > 10:\n        raise ValueError('long')", "    validate(v)\n"),
    ("def validate(v):\n    if not re.fullmatch(r'[a-z0-9]+', v):\n        print('bad')", "    validate(v)\n"),
    ("def ok(v):\n    return bool(re.fullmatch(r'[a-z0-9]+', v))", "    ok(v)\n"),
])
def test_weak_or_unused_validator_still_reports(tmp_path, helper, use):
    assert _helper_count(tmp_path, helper, use) == 1
