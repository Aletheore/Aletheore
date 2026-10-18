import inspect
import sys
from types import ModuleType
from unittest.mock import patch

from aletheore._lazy import lazy_module


def test_module_is_not_imported_until_first_attribute_access():
    name = "json"
    sys.modules.pop(name, None)
    stand_in = lazy_module(name)
    assert name not in sys.modules
    assert stand_in.dumps({"a": 1}) == '{"a": 1}'
    assert name in sys.modules


def test_attribute_writes_and_deletes_forward_to_the_real_module():
    stand_in = lazy_module("json")
    stand_in.some_new_attr = "x"
    import json

    assert json.some_new_attr == "x"
    del stand_in.some_new_attr
    assert not hasattr(json, "some_new_attr")


def test_mock_patch_on_a_lazily_loaded_module_still_works():
    stand_in = lazy_module("json")
    with patch.object(stand_in, "dumps", return_value="patched"):
        assert stand_in.dumps({}) == "patched"


def test_dir_reflects_the_real_modules_attributes():
    stand_in = lazy_module("json")
    assert "dumps" in dir(stand_in)


def test_isinstance_and_ismodule_see_the_real_modules_type():
    # Flash Review catch: without a __class__ proxy, isinstance(stand_in, ModuleType) and
    # inspect.ismodule(stand_in) (which is isinstance under the hood) would see _LazyModule
    # instead of the real module's type - a behavioral difference from a real import.
    stand_in = lazy_module("json")
    assert isinstance(stand_in, ModuleType)
    assert inspect.ismodule(stand_in)
