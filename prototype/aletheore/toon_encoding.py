# The PyPI package is "python-toon" (xaviviro/python-toon) - it installs itself
# as the top-level module "toon", not "python_toon". The name "toon" alone is
# ALSO a real, unrelated PyPI package ("Tools for neuroscience experiments",
# aforren1/toon) - confirmed by actually inspecting it, not assumed. There is
# no collision today since aletheore doesn't depend on anything neuroscience-
# related, but this is why the dependency is pinned specifically to
# "python-toon", not the ambiguous bare name.
#
# The TOON spec's own reference implementation (PyPI: "toon-format") was tried
# first and rejected: its encoder is a literal stub as of 0.1.0
# ("NotImplementedError: TOON encoder is not yet implemented"), confirmed by
# actually calling it, not assumed from the README.
import toon


def to_toon(data: object) -> str:
    return toon.encode(data)
