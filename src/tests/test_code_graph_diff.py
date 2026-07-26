from aletheore.code_graph_diff import diff_endpoints, diff_modules, module_content_hash


def _module(path, **overrides):
    base = {
        "path": path,
        "language": "python",
        "imports": [],
        "imported_by": [],
        "symbols": {"functions": [], "classes": []},
    }
    base.update(overrides)
    return base


def test_module_content_hash_ignores_path_and_imported_by():
    # imported_by is derived from OTHER files' imports, not this module's
    # own content - two modules with identical language/imports/symbols
    # but different imported_by must hash the same, since a change in
    # some other file's imports shouldn't mark THIS file as changed.
    a = _module("a.py", imported_by=["x.py"])
    b = _module("a.py", imported_by=["y.py", "z.py"])
    assert module_content_hash(a) == module_content_hash(b)


def test_module_content_hash_changes_when_symbols_change():
    a = _module("a.py", symbols={"functions": [], "classes": []})
    b = _module("a.py", symbols={"functions": [{"name": "f", "start_line": 1, "end_line": 2}], "classes": []})
    assert module_content_hash(a) != module_content_hash(b)


def test_module_content_hash_changes_when_imports_change():
    a = _module("a.py", imports=["b.py"])
    b = _module("a.py", imports=["c.py"])
    assert module_content_hash(a) != module_content_hash(b)


def test_module_content_hash_is_stable_regardless_of_import_order():
    a = _module("a.py", imports=["b.py", "c.py"])
    b = _module("a.py", imports=["c.py", "b.py"])
    assert module_content_hash(a) == module_content_hash(b)


def test_diff_modules_returns_new_file_as_changed():
    modules = [_module("a.py")]
    changed, deleted = diff_modules({}, modules)

    assert len(changed) == 1
    assert changed[0]["path"] == "a.py"
    assert changed[0]["content_hash"] == module_content_hash(modules[0])
    assert deleted == []


def test_diff_modules_skips_unchanged_file():
    module = _module("a.py")
    previous_hashes = {"a.py": module_content_hash(module)}

    changed, deleted = diff_modules(previous_hashes, [module])

    assert changed == []
    assert deleted == []


def test_diff_modules_detects_changed_file():
    old_module = _module("a.py", imports=["b.py"])
    new_module = _module("a.py", imports=["c.py"])
    previous_hashes = {"a.py": module_content_hash(old_module)}

    changed, deleted = diff_modules(previous_hashes, [new_module])

    assert len(changed) == 1
    assert changed[0]["path"] == "a.py"


def test_diff_modules_detects_deleted_file():
    previous_hashes = {"a.py": "some-hash", "b.py": "other-hash"}

    changed, deleted = diff_modules(previous_hashes, [_module("b.py")])

    assert deleted == ["a.py"]


def test_diff_modules_handles_empty_previous_and_current():
    changed, deleted = diff_modules({}, [])
    assert changed == []
    assert deleted == []


def _endpoint(method, path, file, line):
    return {"method": method, "path": path, "file": file, "line": line, "handler": "h"}


def test_diff_endpoints_returns_new_endpoint_as_changed():
    endpoints = [_endpoint("GET", "/users", "app.py", 10)]

    changed, deleted = diff_endpoints({}, endpoints)

    assert changed == endpoints
    assert deleted == []


def test_diff_endpoints_skips_unchanged_endpoint():
    endpoint = _endpoint("GET", "/users", "app.py", 10)
    previous = {("GET", "/users"): {"file": "app.py", "line": 10}}

    changed, deleted = diff_endpoints(previous, [endpoint])

    assert changed == []
    assert deleted == []


def test_diff_endpoints_detects_moved_endpoint():
    endpoint = _endpoint("GET", "/users", "app.py", 25)
    previous = {("GET", "/users"): {"file": "app.py", "line": 10}}

    changed, deleted = diff_endpoints(previous, [endpoint])

    assert changed == [endpoint]
    assert deleted == []


def test_diff_endpoints_detects_removed_endpoint():
    previous = {("GET", "/users"): {"file": "app.py", "line": 10}}

    changed, deleted = diff_endpoints(previous, [])

    assert deleted == [("GET", "/users")]
