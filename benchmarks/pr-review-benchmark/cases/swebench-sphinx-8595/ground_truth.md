SWE-bench Verified instance `sphinx-doc__sphinx-8595` (sphinx-doc/sphinx). autodoc: empty __all__ attribute is ignored
**Describe the bug**
autodoc: empty `__all__` attribute is ignored

**To Reproduce**
```
# example.py
__all__ = []


def foo():
    "docstring"


def bar():
    "docstring"


def baz():
    "docstring"
```
```
# index.rst
.. automodule:: example
   :members:
```

All foo, bar, and baz are shown.

**Expected behavior**
No entries should be shown because `__all__` is empty.

**Your project**
No

**Screenshots**
No

**
