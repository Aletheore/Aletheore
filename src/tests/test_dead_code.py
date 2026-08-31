import re

from aletheore.dead_code import _dotted_path_candidates, find_dead_code


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


def test_capitalized_test_directory_is_never_unreachable(tmp_path):
    # SwiftPM/Xcode universally capitalize the test directory ("Tests/") -
    # real repo confirmed: every test file in apple/swift-algorithms lives
    # under "Tests/", which the previously case-sensitive pattern silently
    # missed entirely (only matched lowercase "test"/"tests"/"__tests__").
    modules = [_module("Tests/SwiftAlgorithmsTests/ChainTests.swift")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_package_swift_manifest_is_never_unreachable(tmp_path):
    # Real repo confirmed (apple/swift-algorithms): Package.swift parses as
    # a legitimate module now that Swift is a supported language - it's the
    # build manifest itself, read by the SwiftPM toolchain, not imported by
    # any of the repo's own application code, same category as manage.py/
    # wsgi.py/conftest.py above.
    modules = [_module("Package.swift")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "Package.swift" in result["entry_points_detected"]


def test_jvm_test_files_are_never_unreachable(tmp_path):
    # Real repo confirmed (android/architecture-samples): androidTest files
    # are invoked by instrumentation/JUnit reflection, never a plain import,
    # so they always look unreachable to the import graph. The PascalCase
    # "*Test.kt" suffix is the JVM convention (unlike Python's "test_*.py"),
    # and "androidTest" is one fused word - not matched by the tests?/
    # __tests__ directory pattern, which requires "test" as its own segment.
    modules = [
        _module(
            "app/src/androidTest/java/com/example/todoapp/tasks/TasksScreenTest.kt"
        ),
        _module("app/src/androidTest/java/com/example/todoapp/data/TaskDaoTest.kt"),
        _module("app/src/test/java/com/example/todoapp/data/TaskRepositoryTest.java"),
        # A test-directory file with no "*Test" suffix (a fixture/helper) -
        # only the directory-convention pattern catches this one.
        _module("app/src/androidTest/java/com/example/todoapp/util/TestUtils.kt"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_android_manifest_application_shorthand_name_is_never_unreachable(tmp_path):
    # Real repo confirmed (android/architecture-samples): TodoApplication.kt
    # carries @HiltAndroidApp and is referenced only by AndroidManifest.xml's
    # <application android:name=".TodoApplication"> - the Android OS
    # instantiates it via reflection from that XML, never a plain Kotlin
    # import. ".TodoApplication" is the manifest shorthand form (relative to
    # the app's package), not a file path or a full class name.
    manifest_dir = tmp_path / "app" / "src" / "main"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <application android:name=".TodoApplication" />\n'
        "</manifest>\n"
    )
    app_dir = tmp_path / "app" / "src" / "main" / "java" / "com" / "example" / "todoapp"
    app_dir.mkdir(parents=True)
    (app_dir / "TodoApplication.kt").write_text(
        "@HiltAndroidApp\nclass TodoApplication : Application()\n"
    )
    modules = [_module("app/src/main/java/com/example/todoapp/TodoApplication.kt")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "app/src/main/java/com/example/todoapp/TodoApplication.kt" in result["entry_points_detected"]


def test_android_manifest_activity_fully_qualified_name_is_never_unreachable(tmp_path):
    # Real repo confirmed (android/architecture-samples): TodoActivity.kt,
    # the app's launcher activity, is referenced only by AndroidManifest.xml's
    # <activity android:name="com.example...TodoActivity"> with a
    # MAIN/LAUNCHER intent-filter - the fully-qualified form, unlike
    # TodoApplication's shorthand above.
    manifest_dir = tmp_path / "app" / "src" / "main"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        "    <application>\n"
        '        <activity android:name="com.example.todoapp.TodoActivity" />\n'
        "    </application>\n"
        "</manifest>\n"
    )
    app_dir = tmp_path / "app" / "src" / "main" / "java" / "com" / "example" / "todoapp"
    app_dir.mkdir(parents=True)
    (app_dir / "TodoActivity.kt").write_text("class TodoActivity : AppCompatActivity()\n")
    modules = [_module("app/src/main/java/com/example/todoapp/TodoActivity.kt")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "app/src/main/java/com/example/todoapp/TodoActivity.kt" in result["entry_points_detected"]


def test_android_manifest_ignores_action_and_category_name_attributes(tmp_path):
    # <action>/<category> tags also carry android:name (e.g.
    # "android.intent.action.MAIN"), but those name Intent actions, never a
    # class - only <application>/<activity>/<service>/<receiver>/<provider>
    # are treated as entry-point-bearing tags.
    manifest_dir = tmp_path / "app" / "src" / "main"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        "    <application>\n"
        '        <activity android:name="com.example.todoapp.TodoActivity">\n'
        "            <intent-filter>\n"
        '                <action android:name="android.intent.action.MAIN" />\n'
        '                <category android:name="android.intent.category.LAUNCHER" />\n'
        "            </intent-filter>\n"
        "        </activity>\n"
        "    </application>\n"
        "</manifest>\n"
    )
    app_dir = tmp_path / "app" / "src" / "main" / "java" / "com" / "example" / "todoapp"
    app_dir.mkdir(parents=True)
    (app_dir / "MAIN.kt").write_text("class MAIN\n")
    (app_dir / "TodoActivity.kt").write_text("class TodoActivity : AppCompatActivity()\n")
    modules = [
        _module("app/src/main/java/com/example/todoapp/MAIN.kt"),
        _module("app/src/main/java/com/example/todoapp/TodoActivity.kt"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [m["path"] for m in result["unreachable_modules"]]
    assert "app/src/main/java/com/example/todoapp/MAIN.kt" in paths
    assert "app/src/main/java/com/example/todoapp/TodoActivity.kt" not in paths


def test_android_manifest_entry_point_skips_an_ambiguous_basename_match(tmp_path):
    # A manifest entry names a class, not a file path - resolved the same
    # way _infer_xcodeproj_swift_targets resolves Xcode target membership:
    # basename search under the repo root, kept only when unambiguous. Two
    # files sharing "Foo.kt" as their basename means the manifest's "Foo"
    # can't be resolved to either one specifically, so neither is treated
    # as an entry point (matches the Xcode resolver's own tie-breaking).
    manifest_dir = tmp_path / "app" / "src" / "main"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "AndroidManifest.xml").write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <application android:name=".Foo" />\n'
        "</manifest>\n"
    )
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "Foo.kt").write_text("class Foo\n")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "Foo.kt").write_text("class Foo\n")
    modules = [_module("a/Foo.kt"), _module("b/Foo.kt")]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [m["path"] for m in result["unreachable_modules"]]
    assert "a/Foo.kt" in paths
    assert "b/Foo.kt" in paths


def test_hilt_android_app_annotation_is_never_unreachable(tmp_path):
    (tmp_path / "TodoApplication.kt").write_text(
        "@HiltAndroidApp\nclass TodoApplication : Application()\n"
    )
    modules = [_module("TodoApplication.kt")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "TodoApplication.kt" in result["entry_points_detected"]


def test_hilt_viewmodel_annotation_is_never_unreachable(tmp_path):
    # Real repo confirmed (android/architecture-samples): TasksViewModel.kt
    # and its sibling ViewModels carry @HiltViewModel + @Inject constructor -
    # instantiated via Hilt's generated factory, never a plain import.
    (tmp_path / "TasksViewModel.kt").write_text(
        "@HiltViewModel\nclass TasksViewModel @Inject constructor() : ViewModel()\n"
    )
    modules = [_module("TasksViewModel.kt")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "TasksViewModel.kt" in result["entry_points_detected"]


def test_dagger_module_with_install_in_is_never_unreachable(tmp_path):
    # Real repo confirmed (android/architecture-samples): DataModules.kt's
    # @Module classes are wired into Hilt's DI graph via @InstallIn, never
    # imported by name anywhere in the app's own source.
    (tmp_path / "DataModules.kt").write_text(
        "@Module\n@InstallIn(SingletonComponent::class)\nobject RepositoryModule\n"
    )
    modules = [_module("DataModules.kt")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "DataModules.kt" in result["entry_points_detected"]


def test_dagger_module_with_test_install_in_is_never_unreachable(tmp_path):
    # Real repo confirmed (android/architecture-samples): DatabaseTestModule.kt
    # and RepositoryTestModule.kt (shared-test source set) use @TestInstallIn
    # rather than @InstallIn - Hilt's variant for a test module that replaces
    # a production one - same DI-wiring mechanism, just for test builds.
    (tmp_path / "DatabaseTestModule.kt").write_text(
        "@Module\n"
        "@TestInstallIn(components = [SingletonComponent::class], replaces = [DatabaseModule::class])\n"
        "object DatabaseTestModule\n"
    )
    modules = [_module("DatabaseTestModule.kt")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "DatabaseTestModule.kt" in result["entry_points_detected"]


def test_dagger_module_without_install_in_stays_unreachable(tmp_path):
    # A bare @Module with no @InstallIn/@TestInstallIn isn't enough on its
    # own - real Hilt/Dagger modules always pair @Module with one of those,
    # and requiring the pair avoids matching an unrelated project's own
    # "Module" concept that happens to reuse the annotation name.
    (tmp_path / "NotActuallyDagger.kt").write_text("@Module\nclass NotActuallyDagger\n")
    modules = [_module("NotActuallyDagger.kt")]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [m["path"] for m in result["unreachable_modules"]]
    assert "NotActuallyDagger.kt" in paths


def test_hilt_dagger_annotation_ignored_outside_jvm_files(tmp_path):
    # The annotation text alone isn't the signal - only a real .kt/.kts/
    # .java file gets this heuristic, the same file-extension gate every
    # other language-specific check here (main guard, @main) already uses.
    (tmp_path / "not_kotlin.py").write_text("# @HiltViewModel\nclass NotKotlin: pass\n")
    modules = [_module("not_kotlin.py")]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [m["path"] for m in result["unreachable_modules"]]
    assert "not_kotlin.py" in paths


def test_main_swift_is_never_unreachable(tmp_path):
    # Real repo confirmed (vapor/api-template): Sources/Run/main.swift is
    # Swift's classic top-level-code entry point (predates @main, still
    # what Vapor's own project template uses) - same category as
    # Package.swift above, not this repo's own application code importing
    # it.
    modules = [_module("Sources/Run/main.swift")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "Sources/Run/main.swift" in result["entry_points_detected"]


def test_swift_target_siblings_of_a_main_entry_point_are_never_unreachable(tmp_path):
    # Real repo confirmed (vapor/penny-bot): a target's @main handler file
    # is imported by nothing outside the target (nothing in Swift *can*
    # import a leaf executable target), and its sibling files within the
    # same target (a repository/service layer) are referenced by the
    # handler with no import statement at all - Swift files within one
    # target see each other implicitly. Both looked equally unreachable
    # before this: the per-file import graph can never show intra-target
    # edges, no matter how well cross-target import resolution works.
    target_dir = tmp_path / "Sources" / "AutoFaqsLambda"
    target_dir.mkdir(parents=True)
    (target_dir / "AutoFaqsHandler.swift").write_text(
        "import AWSLambdaRuntime\n\n@main\nstruct AutoFaqsHandler: LambdaHandler {}\n"
    )
    (target_dir / "S3AutoFaqsRepository.swift").write_text(
        "struct S3AutoFaqsRepository {}\n"
    )
    modules = [
        _module("Sources/AutoFaqsLambda/AutoFaqsHandler.swift"),
        _module("Sources/AutoFaqsLambda/S3AutoFaqsRepository.swift"),
    ]
    result = find_dead_code(tmp_path, modules, config=None, ignored_paths=None)
    assert result["unreachable_modules"] == []


def test_swift_target_with_no_reachable_member_stays_unreachable(tmp_path):
    # An entirely orphaned target (no @main, nothing imports it) should
    # still be flagged - the fix only propagates reachability from a
    # target that's actually reachable some other way, it doesn't make
    # every Swift file immune to dead-code detection.
    target_dir = tmp_path / "Sources" / "Orphan"
    target_dir.mkdir(parents=True)
    (target_dir / "OrphanThing.swift").write_text("struct OrphanThing {}\n")
    modules = [_module("Sources/Orphan/OrphanThing.swift")]
    result = find_dead_code(tmp_path, modules, config=None, ignored_paths=None)
    paths = [m["path"] for m in result["unreachable_modules"]]
    assert "Sources/Orphan/OrphanThing.swift" in paths


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


def _reference_referenced_by_dotted_string(path: str, sources: dict[str, str]) -> bool:
    """Deliberately naive, obviously-correct reimplementation of the
    original per-(candidate, file) scan this file's real implementation
    replaced for speed (profiled real cause of aletheore scan/index taking
    ~4x longer than needed on large repos: 6.9M regex .search() calls, one
    per (candidate, file) pair, ~180s of it on ERPNext's ~1M LOC). Used only
    as the parity test's independent ground truth - never called by
    production code."""
    candidates = _dotted_path_candidates(path)
    if not candidates:
        return False
    patterns = [re.compile(r'["\']' + re.escape(candidate) + r'(?=[.\'"])') for candidate in candidates]
    for other_path, content in sources.items():
        if other_path == path:
            continue
        if any(pattern.search(content) for pattern in patterns):
            return True
    return False


def test_dotted_string_detection_matches_naive_reference_implementation(tmp_path):
    # The real regression guard for the O(candidates x files) -> O(files)
    # rewrite: builds a small corpus deliberately covering the shapes that
    # could diverge (nested paths at several depths, a real dispatch
    # reference, a near-miss substring collision, a file referencing its
    # own dotted path, a reference nested several directories deep, no
    # reference at all) and asserts the real implementation agrees with the
    # naive per-(candidate, file) reference on every one of them - not just
    # that both "look reasonable" on a couple of examples.
    files = {
        "scan_worker/jobs.py": "def run_pr_scan_job():\n    pass\n",
        "scan_worker/scheduler.py": 'queue.enqueue("scan_worker.jobs.run_pr_scan_job")\n',
        "scan_worker/other.py": "# mentions scan_worker.jobsxyz, a near-miss substring, not a real match\n",
        "scan_worker/self_ref.py": '# this file mentions its own path "scan_worker.self_ref.thing" - must not count\n',
        "app/deeply/nested/pkg/mod.py": "def helper():\n    pass\n",
        "app/deeply/nested/registry.py": 'TASKS = {"x": "app.deeply.nested.pkg.mod.helper"}\n',
        "app/orphan_no_reference.py": "def unused():\n    pass\n",
        "app/orphan_partial_match.py": (
            "def unused():\n    pass\n"
            "# elsewhere: \"app.orphan_partial\" appears but never continues to "
            "\".match\" - candidate app.orphan_partial_match should not match this\n"
        ),
        "app/prose_false_positive.py": "def unused():\n    pass\n",
        "app/prose_false_positive_ref.py": (
            'log.info("app.prose_false_positive completed successfully")\n'
        ),
    }
    unreachable_candidates = [
        "scan_worker/jobs.py",
        "scan_worker/other.py",
        "scan_worker/self_ref.py",
        "app/deeply/nested/pkg/mod.py",
        "app/orphan_no_reference.py",
        "app/orphan_partial_match.py",
        "app/prose_false_positive.py",
    ]

    for candidate_path in unreachable_candidates:
        expected = _reference_referenced_by_dotted_string(candidate_path, files)

        for path, content in files.items():
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(content)
        modules = [
            _module(p, imported_by=[] if p in unreachable_candidates else ["somewhere/else.py"])
            for p in files
        ]
        result = find_dead_code(tmp_path, modules, config=None)
        actually_rescued = candidate_path in result["entry_points_detected"]

        assert actually_rescued == expected, (
            f"{candidate_path}: real implementation says rescued={actually_rescued}, "
            f"naive reference says {expected}"
        )


def test_dotted_string_detection_real_corpus_full_parity(tmp_path):
    # Same corpus as above, run through find_dead_code once (as production
    # actually calls it - all unreachable candidates checked together, not
    # one at a time), asserting the whole entry_points_detected/
    # unreachable_modules split matches what the naive per-candidate
    # reference would produce for the corpus as a whole.
    files = {
        "scan_worker/jobs.py": "def run_pr_scan_job():\n    pass\n",
        "scan_worker/scheduler.py": 'queue.enqueue("scan_worker.jobs.run_pr_scan_job")\n',
        "scan_worker/other.py": "# mentions scan_worker.jobsxyz, a near-miss substring, not a real match\n",
        "app/deeply/nested/pkg/mod.py": "def helper():\n    pass\n",
        "app/deeply/nested/registry.py": 'TASKS = {"x": "app.deeply.nested.pkg.mod.helper"}\n',
        "app/orphan_no_reference.py": "def unused():\n    pass\n",
        "app/prose_false_positive.py": "def unused():\n    pass\n",
        "app/prose_false_positive_ref.py": (
            'log.info("app.prose_false_positive completed successfully")\n'
        ),
    }
    for path, content in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content)

    unreachable_candidates = [
        "scan_worker/jobs.py",
        "scan_worker/other.py",
        "app/deeply/nested/pkg/mod.py",
        "app/orphan_no_reference.py",
        "app/prose_false_positive.py",
    ]
    expected_rescued = {
        p for p in unreachable_candidates if _reference_referenced_by_dotted_string(p, files)
    }

    modules = [
        _module(p, imported_by=[] if p in unreachable_candidates else ["somewhere/else.py"])
        for p in files
    ]
    result = find_dead_code(tmp_path, modules, config=None)

    actually_rescued = {p for p in unreachable_candidates if p in result["entry_points_detected"]}
    assert actually_rescued == expected_rescued
