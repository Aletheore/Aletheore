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


def test_script_with_main_guard_is_never_unreachable(tmp_path):
    # Found on this repo: RQ worker processes and standalone CLI scripts are
    # run directly (`python -m scan_worker.worker`, `python scripts/foo.py`),
    # never imported by another module - a `__main__` guard is a strong
    # signal that a file is meant to be invoked that way, filename aside.
    (tmp_path / "worker.py").write_text(
        "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"
    )
    modules = [_module("worker.py")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "worker.py" in result["entry_points_detected"]


def test_script_without_main_guard_is_still_unreachable(tmp_path):
    # A __main__-guard file with no other references is a legitimate,
    # deliberately-invoked entry point (see above) - a plain orphan module
    # with no guard and no importer is not, and must still be flagged. This
    # is the false-negative guard rail on the fix above.
    (tmp_path / "orphan.py").write_text("def helper():\n    pass\n")
    modules = [_module("orphan.py")]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [module["path"] for module in result["unreachable_modules"]]
    assert "orphan.py" in paths


def test_conftest_py_is_never_unreachable(tmp_path):
    # pytest auto-discovers conftest.py by filename alone - never imported by
    # test files or anything else, which is the whole point of the convention.
    modules = [_module("tests/conftest.py"), _module("github-app/tests/conftest.py")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert set(result["entry_points_detected"]) == {"tests/conftest.py", "github-app/tests/conftest.py"}


def test_module_dispatched_by_dotted_string_is_never_unreachable(tmp_path):
    # Found on this repo: RQ's queue.enqueue("scan_worker.jobs.<fn>", ...) dispatches
    # by dotted-string module path, never a Python import - scan_worker/jobs.py, the
    # busiest module in the worker, looked completely unreachable without this check.
    (tmp_path / "scan_worker").mkdir()
    (tmp_path / "scan_worker" / "jobs.py").write_text("def run_pr_scan_job():\n    pass\n")
    (tmp_path / "scan_worker" / "scheduler.py").write_text(
        'queue.enqueue("scan_worker.jobs.run_pr_scan_job")\n'
    )
    modules = [
        _module("scan_worker/jobs.py"),
        _module("scan_worker/scheduler.py", imported_by=["scan_worker/worker.py"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "scan_worker/jobs.py" in result["entry_points_detected"]


def test_module_referenced_only_by_unrelated_substring_is_still_unreachable(tmp_path):
    # False-negative guard rail: a module whose name merely happens to be a substring
    # of something else in the repo (not a real dotted-path dispatch reference) must
    # still be flagged - this isn't a license to treat any name collision as reachable.
    (tmp_path / "scan_worker").mkdir()
    (tmp_path / "scan_worker" / "jobs.py").write_text("def helper():\n    pass\n")
    (tmp_path / "scan_worker" / "other.py").write_text(
        "# unrelated comment mentioning scan_worker.jobsxyz elsewhere\n"
    )
    modules = [
        _module("scan_worker/jobs.py"),
        _module("scan_worker/other.py", imported_by=["scan_worker/worker.py"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [module["path"] for module in result["unreachable_modules"]]
    assert "scan_worker/jobs.py" in paths


def test_js_referenced_by_html_script_tag_is_never_unreachable(tmp_path):
    # Found on this repo: plain <script src="..."> tags (no bundler, no ES
    # module imports) load website JS - the import graph never sees these
    # references, so every one of these files looked unreachable.
    (tmp_path / "index.html").write_text(
        '<html><body><script src="script.js"></script></body></html>'
    )
    (tmp_path / "script.js").write_text("console.log('hi');")
    modules = [_module("script.js")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "script.js" in result["entry_points_detected"]


def test_js_not_referenced_by_any_html_is_still_unreachable(tmp_path):
    (tmp_path / "index.html").write_text('<html><body>no scripts here</body></html>')
    modules = [_module("orphan.js")]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [module["path"] for module in result["unreachable_modules"]]
    assert "orphan.js" in paths


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
