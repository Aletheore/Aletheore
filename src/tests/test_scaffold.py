import tomllib
from pathlib import Path

import aletheore


def test_package_importable():
    assert aletheore.__version__ == "0.8.7"


def test_declared_version_matches_packaging_metadata():
    """`__version__` and pyproject's version must not drift apart.

    They did, for five releases: 0.8.0 through 0.8.4 each bumped
    `__version__` while pyproject stayed at 0.7.2, so `aletheore --version`
    and `aletheore status` - both of which read
    importlib.metadata.version("aletheore"), not `__version__` - reported
    0.7.2 to every user of every one of those releases, and the built
    artefact could never be published, because 0.7.2 was already on PyPI.

    Asserted against pyproject on disk rather than
    importlib.metadata.version(), which reports whatever was last installed
    and so fails spuriously against a stale editable install.
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert declared == aletheore.__version__
