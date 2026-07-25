from aletheore.dead_code import find_dead_code


def _module(path, imported_by=None):
    return {"path": path, "imports": [], "imported_by": imported_by or []}


def test_module_with_no_imported_by_is_unreachable(tmp_path):
    modules = [_module("app/orphan.py"), _module("app/used.py", imported_by=["app/main.py"])]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [module["path"] for module in result["unreachable_modules"]]
    assert "app/orphan.py" in paths
    assert "app/used.py" not in paths


def test_recognized_entry_point_is_never_unreachable(tmp_path):
    modules = [_module("main.py"), _module("app/__main__.py"), _module("index.js")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert set(result["entry_points_detected"]) == {"main.py", "app/__main__.py", "index.js"}


def test_test_files_are_never_unreachable(tmp_path):
    modules = [
        _module("tests/test_thing.py"),
        _module("src/thing_test.py"),
        _module("src/__tests__/thing.test.js"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_config_can_add_custom_entry_points(tmp_path):
    modules = [_module("app/worker.py")]
    config = {"dead_code_entry_points": ["app/worker.py"]}
    result = find_dead_code(tmp_path, modules, config=config)
    assert result["unreachable_modules"] == []
    assert "app/worker.py" in result["entry_points_detected"]


def test_unused_dependency_flagged_when_never_imported(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\nflask==3.0.0\n")
    modules = [_module("app/main.py")]
    modules[0]["imports"] = ["flask"]
    result = find_dead_code(tmp_path, modules, config=None)
    unused = {(dependency["ecosystem"], dependency["package"]) for dependency in result["unused_dependencies"]}
    assert ("PyPI", "requests") in unused
    assert ("PyPI", "flask") not in unused


def test_npm_transitive_lockfile_dependencies_are_never_reported_as_unused(tmp_path):
    # Confirmed on this repo: a package-lock.json's "packages" map lists every
    # resolved transitive dependency, not just what's declared in package.json
    # - checking all of them against import statements flagged ~200 of
    # cspell's own transitive packages as "unused" even though only cspell
    # itself is a real, directly-declared dependency your code could ever
    # import. Only package.json's direct dependencies are valid candidates.
    import json

    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {"cspell": "^8.17.5"}}))
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "": {"devDependencies": {"cspell": "^8.17.5"}},
                    "node_modules/cspell": {"version": "8.17.5"},
                    "node_modules/cspell-lib": {"version": "8.17.5"},
                    "node_modules/chalk": {"version": "5.3.0"},
                }
            }
        )
    )
    modules = [_module("app/index.js")]
    result = find_dead_code(tmp_path, modules, config=None)
    unused = {dependency["package"] for dependency in result["unused_dependencies"]}
    assert "cspell-lib" not in unused
    assert "chalk" not in unused
    assert "cspell" in unused
