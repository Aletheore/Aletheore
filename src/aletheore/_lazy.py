"""Deferred module imports for heavy third-party packages.

`lazy_module("httpx")` returns a stand-in that imports the real module on first
attribute access, so `import aletheore.cli` doesn't pay for packages a given
command never touches. Attribute reads, writes and deletes all forward to the
real module, so `unittest.mock.patch("pkg.httpx.get")` keeps working.

Annotations that mention the stand-in (e.g. `httpx.Client | None`) are
evaluated at definition time and would trigger the import, so modules using
this must have `from __future__ import annotations`.
"""

import importlib
from types import ModuleType


class _LazyModule:
    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_module", None)

    def _load(self) -> ModuleType:
        module = object.__getattribute__(self, "_module")
        if module is None:
            module = importlib.import_module(object.__getattribute__(self, "_name"))
            object.__setattr__(self, "_module", module)
        return module

    def __getattr__(self, attr: str):
        return getattr(self._load(), attr)

    def __setattr__(self, attr: str, value) -> None:
        setattr(self._load(), attr, value)

    def __delattr__(self, attr: str) -> None:
        delattr(self._load(), attr)


def lazy_module(name: str) -> ModuleType:
    return _LazyModule(name)  # type: ignore[return-value]
