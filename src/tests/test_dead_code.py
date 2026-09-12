import re

from aletheore.dead_code import _dotted_path_candidates, _raw_external_import_roots, find_dead_code


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


def test_django_admin_py_is_never_unreachable(tmp_path):
    # Real gap found via audit against a real repo (wagtail/wagtail):
    # django.contrib.admin.autodiscover() (called automatically by
    # AdminConfig.ready() in every modern Django project) dynamically
    # imports every installed app's own admin.py by convention - never a
    # plain top-level import anywhere in the app's own source. Confirmed:
    # wagtail/documents/admin.py and wagtail/images/admin.py both carry
    # real, live admin registrations and zero imported_by.
    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    (app_dir / "admin.py").write_text(
        "from django.contrib import admin\n\nfrom myapp.models import Widget\n\n"
        "admin.site.register(Widget)\n"
    )
    modules = [_module("myapp/admin.py")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "myapp/admin.py" in result["entry_points_detected"]


def test_unrelated_non_django_admin_py_is_not_exempted(tmp_path):
    # Real false-negative risk found via peer review of this PR: "admin.py"
    # is a common enough filename outside Django (this same module's own
    # routes.rb addition explicitly rejected Laravel's web.php/api.php on
    # this exact risk) that an unconditional basename match would exempt a
    # genuinely dead, unrelated admin.py with no Django import at all.
    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    (app_dir / "admin.py").write_text(
        "def totally_unrelated_dead_function():\n    pass\n"
    )
    modules = [_module("myapp/admin.py")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "myapp/admin.py" in [m["path"] for m in result["unreachable_modules"]]


def test_test_files_are_never_unreachable(tmp_path):
    modules = [
        _module("tests/test_thing.py"),
        _module("src/thing_test.py"),
        _module("src/__tests__/thing.test.js"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_rspec_spec_files_are_never_unreachable(tmp_path):
    # RSpec - Ruby's dominant test framework - names its files with a
    # "_spec.rb" suffix, not a "test(s)" directory, so the generic
    # tests?/__tests__ pattern never matched them. Real repo confirmed
    # (discourse/discourse): 3,799 real *_spec.rb files across the main
    # app and its plugins/migrations subprojects were completely
    # unmatched, dwarfing the false-positive count #664/#666 fixed for
    # the same codebase.
    modules = [
        _module("spec/models/user_spec.rb"),
        _module("spec/lib/discourse/converter_spec.rb"),
        _module("plugins/discourse-events/spec/jobs/some_job_spec.rb"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_rspec_support_files_are_never_unreachable(tmp_path):
    # A spec/ directory also holds non-suffixed test infrastructure
    # (spec_helper.rb, factories, shared examples, custom matchers) that
    # is just as much test-only code as the specs themselves. Real repo
    # confirmed (discourse/discourse): 711 such files exist alongside the
    # 3,799 *_spec.rb files above.
    modules = [
        _module("spec/spec_helper.rb"),
        _module("spec/support/matchers/have_constant.rb"),
        _module("spec/factories/users.rb"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_ruby_file_outside_spec_directory_still_unreachable(tmp_path):
    # The new spec/ patterns must stay scoped to the RSpec convention -
    # a Ruby file that merely mentions "spec" in its own name outside a
    # spec/ directory, or lives under an unrelated directory, is regular
    # application code and must still be flagged when nothing imports it.
    modules = [_module("app/models/inspector.rb")]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [module["path"] for module in result["unreachable_modules"]]
    assert "app/models/inspector.rb" in paths


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


def test_kotlin_package_sibling_of_an_imported_file_is_never_unreachable(tmp_path):
    # Real repo confirmed (android/architecture-samples): ModelMappingExt.kt
    # is called (toExternal()) by DefaultTaskRepository.kt with zero import
    # statement - both declared in the same package, and Kotlin files in one
    # package see each other's top-level declarations with no import at all.
    # DefaultTaskRepository.kt is independently reachable via a real import
    # elsewhere; that reachability should propagate to its package-mate.
    pkg_dir = tmp_path / "data"
    pkg_dir.mkdir()
    (pkg_dir / "ModelMappingExt.kt").write_text(
        "package com.example.todoapp.data\n\nfun LocalTask.toExternal() = Task()\n"
    )
    (pkg_dir / "DefaultTaskRepository.kt").write_text(
        "package com.example.todoapp.data\n\nclass DefaultTaskRepository\n"
    )
    modules = [
        _module("data/ModelMappingExt.kt"),
        _module("data/DefaultTaskRepository.kt", imported_by=["di/DataModules.kt"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_kotlin_package_sibling_of_a_hilt_reachable_file_is_never_unreachable(tmp_path):
    # Real repo confirmed (android/architecture-samples): StatisticsUtils.kt
    # is unreferenced by any import, but shares its package with the
    # @HiltViewModel-annotated StatisticsViewModel.kt - reachability
    # propagation has to consider the SAME signals find_dead_code already
    # treats as reachable on their own (a Hilt/Dagger annotation, not just
    # imported_by), or this exact real case would still be missed.
    pkg_dir = tmp_path / "statistics"
    pkg_dir.mkdir()
    (pkg_dir / "StatisticsUtils.kt").write_text(
        "package com.example.todoapp.statistics\n\nfun getActiveAndCompletedStats() = 1\n"
    )
    (pkg_dir / "StatisticsViewModel.kt").write_text(
        "package com.example.todoapp.statistics\n\n"
        "@HiltViewModel\nclass StatisticsViewModel @Inject constructor() : ViewModel()\n"
    )
    modules = [
        _module("statistics/StatisticsUtils.kt"),
        _module("statistics/StatisticsViewModel.kt"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_kotlin_package_with_no_reachable_member_stays_unreachable(tmp_path):
    # An entirely orphaned package (nothing in it independently reachable)
    # should still be flagged - the fix only propagates reachability from a
    # package that's actually reachable some other way, the same boundary
    # test_swift_target_with_no_reachable_member_stays_unreachable already
    # covers for Swift's target-level version of this.
    pkg_dir = tmp_path / "orphan"
    pkg_dir.mkdir()
    (pkg_dir / "A.kt").write_text("package com.example.orphan\n\nclass A\n")
    (pkg_dir / "B.kt").write_text("package com.example.orphan\n\nclass B\n")
    modules = [_module("orphan/A.kt"), _module("orphan/B.kt")]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [m["path"] for m in result["unreachable_modules"]]
    assert "orphan/A.kt" in paths
    assert "orphan/B.kt" in paths


def test_kotlin_package_reachability_does_not_leak_from_a_test_file(tmp_path):
    # Deliberate: is_test_file(path) files are excluded from package
    # grouping entirely, not just left to independently fall through -
    # Android's own androidTest/test convention constantly gives a test
    # file the SAME package name as the production code it tests, so
    # without this exclusion a test file's own reachability (nothing
    # imports a test file itself, but this simulates the shape) could leak
    # into an unrelated, genuinely-unused production sibling.
    pkg_dir = tmp_path / "prod"
    pkg_dir.mkdir()
    (pkg_dir / "Orphan.kt").write_text("package com.example.pkg\n\nclass Orphan\n")
    (pkg_dir / "FooTest.kt").write_text("package com.example.pkg\n\nclass FooTest\n")
    modules = [
        _module("prod/Orphan.kt"),
        _module("prod/FooTest.kt", imported_by=["somewhere/Caller.kt"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [m["path"] for m in result["unreachable_modules"]]
    assert "prod/Orphan.kt" in paths


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


def test_go_package_main_func_main_is_never_unreachable(tmp_path):
    # Real bug found via audit: a Go binary's entry point is never imported
    # by anything in the repo (nothing in Go *can* import package main), so
    # it always looked unreachable by the plain imported_by signal alone -
    # same category as Python's __main__ guard and Swift's @main, just for
    # Go's own real convention (package main + func main()).
    (tmp_path / "main.go").write_text(
        "package main\n\nimport \"example.com/app/internal/util\"\n\n"
        "func main() {\n\tutil.Hello()\n}\n"
    )
    modules = [_module("main.go")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "main.go" in result["entry_points_detected"]


def test_go_func_main_without_package_main_is_still_unreachable(tmp_path):
    # A file merely containing a function named "main" in some other
    # package isn't a real binary entry point - both signals (package main
    # AND func main()) are required, not just the function name alone.
    (tmp_path / "helper.go").write_text("package util\n\nfunc main() {}\n")
    modules = [_module("helper.go")]
    result = find_dead_code(tmp_path, modules, config=None)
    paths = [m["path"] for m in result["unreachable_modules"]]
    assert "helper.go" in paths


def test_rust_fn_main_is_never_unreachable(tmp_path):
    # Real bug found via audit: same shape as Go above - Cargo's binary
    # entry point (src/main.rs's fn main(), or any src/bin/*.rs) is never
    # imported by anything else in the crate.
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.rs").write_text("mod util;\n\nfn main() {\n    util::hello();\n}\n")
    modules = [_module("src/main.rs")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "src/main.rs" in result["entry_points_detected"]


def test_java_public_static_void_main_is_never_unreachable(tmp_path):
    # Real bug found via audit: same shape again for Java's real entry-point
    # convention - unlike Python's main.py/__main__.py filename heuristic,
    # Java's entry class can be named anything, so this has to be a content
    # check on the actual method signature, not a filename.
    pkg_dir = tmp_path / "src" / "main" / "java" / "com" / "example"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "Main.java").write_text(
        "package com.example;\n\npublic class Main {\n"
        "    public static void main(String[] args) {\n        Helper.hello();\n    }\n}\n"
    )
    modules = [_module("src/main/java/com/example/Main.java")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "src/main/java/com/example/Main.java" in result["entry_points_detected"]


def test_java_package_sibling_of_the_main_method_is_never_unreachable(tmp_path):
    # Real bug found via audit: same-package Java classes need no import
    # statement either (JLS 7.5.1), the identical rule the Kotlin package-
    # propagation fix already covers - but this only grouped .kt/.kts files
    # before, so Helper.java (referenced only by Main.java, no import)
    # still looked unreachable even after Main.java itself is recognized as
    # an entry point.
    pkg_dir = tmp_path / "src" / "main" / "java" / "com" / "example"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "Main.java").write_text(
        "package com.example;\n\npublic class Main {\n"
        "    public static void main(String[] args) {\n        Helper.hello();\n    }\n}\n"
    )
    (pkg_dir / "Helper.java").write_text(
        "package com.example;\n\npublic class Helper {\n    public static void hello() {}\n}\n"
    )
    modules = [
        _module("src/main/java/com/example/Main.java"),
        _module("src/main/java/com/example/Helper.java"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_csharp_static_main_method_is_never_unreachable(tmp_path):
    # Real bug found via audit: C#'s Main is the same shape once more - the
    # process entry point, never imported (C# doesn't even use imports for
    # local resolution the way Java/Python do).
    (tmp_path / "Program.cs").write_text(
        "using System;\n\nclass Program {\n"
        "    static void Main(string[] args) {\n        Helper.Hello();\n    }\n}\n"
    )
    modules = [_module("Program.cs")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "Program.cs" in result["entry_points_detected"]


def test_rust_nested_fn_main_in_a_test_module_is_not_a_crate_entry_point(tmp_path):
    # Flash Review finding on PR #594: the original `^\s*fn\s+main\s*\(`
    # allowed arbitrary leading whitespace, so a library file's nested
    # `mod tests { fn main() {} }` (rustfmt always indents block contents)
    # was treated as if it were the crate's real, unimported binary entry
    # point. The real fn main() below is unindented (column 0); the one
    # inside mod tests is indented and must not match.
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "lib.rs").write_text(
        "pub fn helper() {}\n\n#[cfg(test)]\nmod tests {\n    fn main() {}\n}\n"
    )
    modules = [_module("src/lib.rs")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "src/lib.rs" in [m["path"] for m in result["unreachable_modules"]]
    assert "src/lib.rs" not in result["entry_points_detected"]


def test_java_main_with_extra_modifiers_in_any_order_is_still_an_entry_point(tmp_path):
    # Flash Review finding on PR #594: the original pattern only matched
    # the exact sequences "public static" or "static public" immediately
    # before "void main(" - real, legal Java main methods can carry extra
    # modifiers (final, synchronized) in any position, e.g.
    # `public final static void main(...)` or
    # `public static synchronized void main(...)`.
    pkg_dir = tmp_path / "src" / "main" / "java" / "com" / "example"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "Main.java").write_text(
        "package com.example;\n\npublic class Main {\n"
        "    public final static synchronized void main(String[] args) {\n"
        "        Helper.hello();\n    }\n}\n"
    )
    modules = [_module("src/main/java/com/example/Main.java")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "src/main/java/com/example/Main.java" in result["entry_points_detected"]


def test_csharp_static_helper_and_separate_instance_main_is_not_an_entry_point(tmp_path):
    # Flash Review finding on PR #594: the original check searched the
    # whole file independently for "static" and "Main(" as two separate
    # signals, so a file with an unrelated static helper method AND a
    # separate non-static instance Main() was wrongly treated as having a
    # real static entry point - they must co-occur on the same
    # declaration, not just both appear somewhere in the file.
    (tmp_path / "Program.cs").write_text(
        "using System;\n\nclass Program {\n"
        "    static int Helper() { return 1; }\n"
        "    void Main() { RunThing(); }\n}\n"
    )
    modules = [_module("Program.cs")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "Program.cs" in [m["path"] for m in result["unreachable_modules"]]
    assert "Program.cs" not in result["entry_points_detected"]


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


def test_unused_dependency_check_reflects_a_real_scan_not_a_hand_built_modules_list(tmp_path):
    # Found via security-scanner-benchmark's overnight dead-code pilot,
    # then confirmed at real scale (Flask's own actual dependencies -
    # werkzeug, jinja2, itsdangerous, click, blinker, importlib-metadata -
    # were ALL reported unused when scanned for real). Root cause: the
    # test above (and every other unused_dependencies test in this file)
    # hand-sets modules[0]["imports"] = ["flask"] - a raw package name -
    # but scanner/graph.py's resolved_imports (what a real scan actually
    # produces for this field) only ever contains OTHER FILES INSIDE THE
    # REPO that an import successfully resolved to; an external package
    # import that never resolves to a repo-internal file is silently
    # dropped there and never appears in "imports" at all. Every
    # hand-built modules list in this file was accidentally testing a
    # shape the real scanner never produces. This test goes through a
    # real scan_repository() instead, so it would have caught the bug the
    # rest of this file's tests structurally could not.
    from aletheore.evidence import scan_repository

    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    (tmp_path / "app.py").write_text("import requests\n\nrequests.get('https://example.com')\n")

    evidence = scan_repository(
        tmp_path,
        check_vulnerabilities=False,
        scan_git_history=False,
        check_licenses=False,
        map_endpoints=False,
        map_schema=False,
    )

    assert evidence["repository"]["dead_code"]["unused_dependencies"] == []


def test_unused_dependency_check_recognizes_a_used_scoped_npm_package(tmp_path):
    # Real bug found via audit: a scoped npm dependency was always
    # reported unused even when genuinely imported (see
    # _import_root's docstring) - end-to-end through find_dead_code, the
    # actual entry point customers see findings from.
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"@testing-library/react": "^14.0.0", "normalize.css": "^8.0.1"}}'
    )
    (tmp_path / "index.js").write_text(
        "import { render } from '@testing-library/react';\n"
        "import 'normalize.css';\n"
    )
    modules = [{"path": "index.js", "imports": [], "imported_by": []}]
    result = find_dead_code(tmp_path, modules, config=None)
    unused = {(d["ecosystem"], d["package"]) for d in result["unused_dependencies"]}
    assert ("npm", "@testing-library/react") not in unused
    assert ("npm", "normalize.css") not in unused


def test_import_root_only_splits_on_dot_for_python_not_js(tmp_path):
    from aletheore.dead_code import _import_root

    # Python's own dotted-submodule convention: `import os.path` -> "os".
    assert _import_root("os.path") == "os"
    # No such convention in npm - a literal dot is part of the package's
    # own real name, not a submodule separator.
    assert _import_root("chart.js", dotted=False) == "chart.js"


def test_raw_external_import_roots_finds_python_plain_and_from_imports(tmp_path):
    (tmp_path / "app.py").write_text(
        "import requests\nfrom flask import Flask\nfrom . import local_module\n"
    )
    modules = [{"path": "app.py", "imports": [], "imported_by": []}]
    roots = _raw_external_import_roots(tmp_path, modules)
    assert "requests" in roots
    assert "flask" in roots
    # A relative from-import (`from . import ...`) has no external package
    # name - must not contribute a bogus root.
    assert "local_module" not in roots


def test_raw_external_import_roots_finds_js_import_and_require(tmp_path):
    (tmp_path / "index.js").write_text(
        "import _ from 'lodash';\nconst axios = require('axios');\n"
    )
    modules = [{"path": "index.js", "imports": [], "imported_by": []}]
    roots = _raw_external_import_roots(tmp_path, modules)
    assert "lodash" in roots
    assert "axios" in roots


def test_raw_external_import_roots_finds_side_effect_and_dynamic_js_imports(tmp_path):
    # Real bug found via audit: `import '...'` (no `from` clause - CSS/
    # polyfill side-effect imports) and `import('...')` (dynamic/
    # code-split imports) were both missing from the regex, so a package
    # imported only one of these two ways was reported unused every time.
    (tmp_path / "index.js").write_text(
        "import 'normalize.css';\n"
        "const loadChart = () => import('chart.js').then((m) => m.default);\n"
    )
    modules = [{"path": "index.js", "imports": [], "imported_by": []}]
    roots = _raw_external_import_roots(tmp_path, modules)
    # Real npm package names can contain a literal dot with no submodule
    # meaning (unlike Python) - must not be truncated at it.
    assert "normalize.css" in roots
    assert "chart.js" in roots


def test_raw_external_import_roots_finds_require_resolve(tmp_path):
    # Real bug found via audit: `require\(` only matches the literal
    # substring "require(", which never appears in "require.resolve(" -
    # a real, common shape for webpack aliasing and worker entry points
    # (e.g. `new Worker(require.resolve('./worker'))`). A package imported
    # only this way was always reported as unused.
    (tmp_path / "index.js").write_text(
        "const p = require.resolve('lodash');\n"
        "const w = require.resolve( 'worker-farm' );\n"
    )
    modules = [{"path": "index.js", "imports": [], "imported_by": []}]
    roots = _raw_external_import_roots(tmp_path, modules)
    assert "lodash" in roots
    assert "worker_farm" in roots


def test_unused_dependency_check_recognizes_a_package_used_only_via_require_resolve(tmp_path):
    # End-to-end through find_dead_code, the actual entry point customers
    # see findings from - not just the regex-level check above.
    (tmp_path / "package.json").write_text('{"dependencies": {"lodash": "^4.17.21"}}')
    (tmp_path / "index.js").write_text("const p = require.resolve('lodash');\n")
    modules = [{"path": "index.js", "imports": [], "imported_by": []}]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unused_dependencies"] == []


def test_raw_external_import_roots_resolves_scoped_npm_packages_to_scope_and_name(tmp_path):
    # Real bug found via audit: _import_root split on the first "/" for
    # every import, truncating a scoped npm package (`@scope/name`) down
    # to just its bare scope (`@testing_library`) - which could never
    # match _package_import_names' normalization of the real package.json
    # key (`@testing_library/react`), so every scoped dependency
    # (@angular/*, @babel/*, @nestjs/*, @vue/*, @types/*, etc.) was
    # unconditionally flagged unused.
    (tmp_path / "index.js").write_text(
        "import { render } from '@testing-library/react';\n"
        "import Button from '@mui/material/Button';\n"
    )
    modules = [{"path": "index.js", "imports": [], "imported_by": []}]
    roots = _raw_external_import_roots(tmp_path, modules)
    assert "@testing_library/react" in roots
    assert "@testing_library" not in roots
    # A subpath import of a scoped package resolves to the installed
    # package (scope + name), not the file within it.
    assert "@mui/material" in roots
    assert "@mui/material/button" not in roots


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


def test_spring_boot_rest_controller_is_never_unreachable(tmp_path):
    # Real bug confirmed via a real Discourse-scale repo audit (this class
    # of bug generalizes the Rails/Zeitwerk finding below to Java): Spring
    # discovers @RestController classes by classpath component-scanning
    # (@SpringBootApplication's @ComponentScan), never a plain import, so a
    # controller referenced by nothing else in the repo always looked
    # unreachable before this.
    pkg_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "api"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "UserController.java").write_text(
        "package com.example.api;\n"
        "import org.springframework.web.bind.annotation.GetMapping;\n"
        "import org.springframework.web.bind.annotation.RestController;\n\n"
        "@RestController\n"
        "public class UserController {\n"
        "    @GetMapping(\"/users\")\n"
        "    public String listUsers() { return \"users\"; }\n"
        "}\n"
    )
    modules = [_module("src/main/java/com/example/api/UserController.java")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "src/main/java/com/example/api/UserController.java" in result["entry_points_detected"]


def test_spring_stereotype_annotation_requires_springframework_import(tmp_path):
    # A project's own unrelated @Service/@Component class (nothing to do
    # with Spring) must not be swept up just because the annotation name
    # matches - the same corroborating-import requirement as the Dagger
    # @Module + @InstallIn pairing elsewhere in this file.
    pkg_dir = tmp_path / "src" / "main" / "java" / "com" / "example"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "NotSpring.java").write_text(
        "package com.example;\n\n@Service\npublic class NotSpring {}\n"
    )
    modules = [_module("src/main/java/com/example/NotSpring.java")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "src/main/java/com/example/NotSpring.java" in [
        m["path"] for m in result["unreachable_modules"]
    ]


def test_aspnet_core_api_controller_is_never_unreachable(tmp_path):
    # Real bug, same shape as Spring above: ASP.NET Core's AddControllers()
    # discovers [ApiController]/ControllerBase types by assembly scanning
    # at startup, never a plain C# reference.
    controllers_dir = tmp_path / "Controllers"
    controllers_dir.mkdir()
    (controllers_dir / "UsersController.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n\n"
        "namespace MyApp.Controllers {\n"
        "    [ApiController]\n"
        "    [Route(\"api/[controller]\")]\n"
        "    public class UsersController : ControllerBase {\n"
        "        [HttpGet]\n"
        "        public IActionResult Get() { return Ok(); }\n"
        "    }\n"
        "}\n"
    )
    modules = [_module("Controllers/UsersController.cs")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []
    assert "Controllers/UsersController.cs" in result["entry_points_detected"]


def test_aspnet_controller_pattern_requires_aspnetcore_mvc_import(tmp_path):
    # A project's own unrelated "class Foo : Controller" (a base class with
    # nothing to do with ASP.NET Core MVC) must not be swept up just
    # because the base-class name matches.
    (tmp_path / "Foo.cs").write_text(
        "namespace MyApp {\n    class Foo : Controller {}\n}\n"
    )
    modules = [_module("Foo.cs")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "Foo.cs" in [m["path"] for m in result["unreachable_modules"]]


def test_aspnet_controller_via_project_wide_global_using_is_never_unreachable(tmp_path):
    # Real gap found via audit against a real repo (dotnet/eShop): C# 10's
    # `global using` (the default in every `dotnet new` template since
    # .NET 6, almost always collected into one GlobalUsings.cs) applies
    # project-wide from a single declaration, so a real controller file
    # can carry neither a same-file `using Microsoft.AspNetCore.Mvc` nor
    # any other local import at all - confirmed on eShop's own
    # HomeController.cs/ConsentController.cs/etc, none of which declare
    # the import locally. Without checking for a global using anywhere in
    # the repo, this check never fired on a single real controller in a
    # modern (.NET 6+) app.
    (tmp_path / "GlobalUsings.cs").write_text(
        "global using Microsoft.AspNetCore.Mvc;\n"
    )
    quickstart_dir = tmp_path / "Quickstart" / "Home"
    quickstart_dir.mkdir(parents=True)
    (quickstart_dir / "HomeController.cs").write_text(
        "namespace IdentityServerHost.Quickstart.UI\n{\n"
        "    public class HomeController : Controller\n    {\n"
        "        public IActionResult Index() { return View(); }\n    }\n}\n"
    )
    modules = [
        _module("GlobalUsings.cs"),
        _module("Quickstart/Home/HomeController.cs"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "Quickstart/Home/HomeController.cs" in result["entry_points_detected"]


def test_aspnet_global_using_in_obj_build_dir_is_ignored(tmp_path):
    # Real bug found via peer review of this PR: .NET's `obj/` intermediate
    # build directory fills with SDK-generated files, including a
    # GlobalUsings.g.cs that itself declares `global using
    # Microsoft.AspNetCore.Mvc` for every project the SDK builds - this
    # repo's own IGNORED_DIRS already documents exactly that. Without
    # skipping obj/ here the same way _html_script_entry_points already
    # skips it, generated build output from ANY one project would leak
    # this signal repo-wide and wrongly mark an unrelated same-named
    # "Controller" class in a completely different, non-ASP.NET project as
    # an entry point.
    obj_dir = tmp_path / "SomeOtherProject" / "obj" / "Debug"
    obj_dir.mkdir(parents=True)
    (obj_dir / "GlobalUsings.g.cs").write_text(
        "global using Microsoft.AspNetCore.Mvc;\n"
    )
    (tmp_path / "Foo.cs").write_text(
        "namespace MyApp {\n    class Foo : Controller {}\n}\n"
    )
    modules = [_module("Foo.cs")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "Foo.cs" in [m["path"] for m in result["unreachable_modules"]]


def test_aspnet_global_using_does_not_leak_across_csproj_projects(tmp_path):
    # Real bug found via Flash Review on #668, confirmed directly after the
    # obj/ fix above turned out not to address it: a single repo-wide
    # boolean, even with generated build output excluded, still
    # over-applies one project's own global using to a completely
    # unrelated project sharing the same git repository - a very real
    # shape for .NET (eShop, this fix's own real-repo test subject, is
    # itself multi-project: Identity.API, Ordering.API, Catalog.API,
    # Basket.API each their own project).
    (tmp_path / "ProjectA").mkdir()
    (tmp_path / "ProjectA" / "ProjectA.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n'
    )
    (tmp_path / "ProjectA" / "GlobalUsings.cs").write_text(
        "global using Microsoft.AspNetCore.Mvc;\n"
    )
    (tmp_path / "ProjectB").mkdir()
    (tmp_path / "ProjectB" / "ProjectB.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"></Project>\n'
    )
    (tmp_path / "ProjectB" / "Foo.cs").write_text(
        "namespace ProjectB {\n    class Foo : Controller {}\n}\n"
    )
    modules = [
        _module("ProjectA/GlobalUsings.cs"),
        _module("ProjectB/Foo.cs"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    # ProjectB has no ASP.NET Core dependency and no global using of its
    # own - Foo.cs must stay flagged, not get swept up by ProjectA's.
    assert "ProjectB/Foo.cs" in [m["path"] for m in result["unreachable_modules"]]


def test_aspnet_global_using_still_applies_within_its_own_project(tmp_path):
    (tmp_path / "ProjectA").mkdir()
    (tmp_path / "ProjectA" / "ProjectA.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n'
    )
    (tmp_path / "ProjectA" / "GlobalUsings.cs").write_text(
        "global using Microsoft.AspNetCore.Mvc;\n"
    )
    (tmp_path / "ProjectA" / "Foo.cs").write_text(
        "namespace ProjectA {\n    class Foo : Controller {}\n}\n"
    )
    modules = [
        _module("ProjectA/GlobalUsings.cs"),
        _module("ProjectA/Foo.cs"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "ProjectA/Foo.cs" in result["entry_points_detected"]


def test_aspnet_global_using_falls_back_to_repo_wide_with_no_csproj_anywhere(tmp_path):
    # A repo with no .csproj at all (no discoverable project boundary for
    # either the declaring file or the candidate file) can't be scoped to
    # anything real - falls back to the pre-scoping repo-wide behavior for
    # this specific case only, matching every earlier synthetic test in
    # this file that never bothered creating a .csproj fixture.
    (tmp_path / "GlobalUsings.cs").write_text(
        "global using Microsoft.AspNetCore.Mvc;\n"
    )
    (tmp_path / "Foo.cs").write_text(
        "namespace MyApp {\n    class Foo : Controller {}\n}\n"
    )
    modules = [_module("GlobalUsings.cs"), _module("Foo.cs")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "Foo.cs" in result["entry_points_detected"]


def test_aspnet_global_using_in_ambiguous_shared_csproj_dir_is_not_applied(tmp_path):
    # Second, distinct Flash Review finding on #668 after the first
    # project-scoping fix: two .csproj files sharing one directory (a
    # real but rare layout - standard SDK/IDE tooling always gives each
    # project its own directory) means directory alone can't determine
    # project membership. Simply merging this into the "no .csproj found"
    # bucket would NOT actually fix the leak, confirmed by reasoning
    # through it directly before implementing: the declaring file and a
    # candidate file could both sit in this same ambiguous directory, so
    # both would land in the identical fallback bucket and the leak would
    # reproduce exactly as before. Must instead apply NO global-using
    # signal at all to a file in an ambiguous directory, in either
    # direction.
    shared_dir = tmp_path / "SharedDir"
    shared_dir.mkdir()
    (shared_dir / "ProjectA.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n'
    )
    (shared_dir / "ProjectB.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"></Project>\n'
    )
    (shared_dir / "GlobalUsings.cs").write_text(
        "global using Microsoft.AspNetCore.Mvc;\n"
    )
    (shared_dir / "Foo.cs").write_text(
        "namespace SharedDir {\n    class Foo : Controller {}\n}\n"
    )
    modules = [
        _module("SharedDir/GlobalUsings.cs"),
        _module("SharedDir/Foo.cs"),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "SharedDir/Foo.cs" in [m["path"] for m in result["unreachable_modules"]]


def test_rails_controller_named_only_in_routes_rb_is_never_unreachable(tmp_path):
    # Real bug found via a real Discourse scan (68,183-commit clone): Rails
    # dispatches `to: "users#show"` and `resources :name` route entries to
    # controllers by Zeitwerk constant-name autoloading, never a plain
    # `require` - config/routes.rb is the only place either controller
    # below is ever named. 13,261 files (the overwhelming majority of them
    # controllers exactly like these two) were misflagged as dead code on
    # that real repo before this.
    controllers_dir = tmp_path / "app" / "controllers" / "admin"
    controllers_dir.mkdir(parents=True)
    (tmp_path / "app" / "controllers" / "users_controller.rb").write_text(
        "class UsersController < ApplicationController\n  def show; end\nend\n"
    )
    (controllers_dir / "badges_controller.rb").write_text(
        "class Admin::BadgesController < Admin::AdminController\n  def index; end\nend\n"
    )
    modules = [
        _module("app/controllers/users_controller.rb"),
        _module("app/controllers/admin/badges_controller.rb"),
        _module("config/routes.rb"),
    ]
    api_endpoints = [
        {
            "framework": "rails",
            "handler": "users#show",
            "path": "/users/:id",
            "unresolved": False,
            "file": "config/routes.rb",
        },
        {
            # "admin/badges", not bare "badges" - this controller sits under
            # app/controllers/admin/, and endpoints.py's Rails extractor
            # tracks the enclosing `namespace :admin do` block (see
            # _rails_enclosing_module_prefix) precisely so this resolves to
            # the right one of two same-named controllers instead of
            # colliding with an unrelated top-level `badges_controller.rb`.
            "framework": "rails",
            "handler": "resources(...)",
            "path": "admin/badges",
            "unresolved": True,
            "file": "config/routes.rb",
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert result["unreachable_modules"] == []
    # routes.rb itself is a filename-recognized entry point (Rails' router
    # loads it by convention), not resolved via the route-handler path -
    # confirm it separately so this test also locks that in.
    assert "config/routes.rb" in result["entry_points_detected"]


def test_rails_ambiguous_controller_basename_is_left_unresolved(tmp_path):
    # Two controllers with the same base name in different Rails engines/
    # plugins (a real shape - Discourse plugins each keep their own
    # app/controllers/): resolving "widgets" must not guess which one
    # routes.rb meant, so both stay unresolved rather than one being
    # silently (and possibly wrongly) marked reachable.
    (tmp_path / "app" / "controllers").mkdir(parents=True)
    (tmp_path / "plugins" / "a" / "app" / "controllers").mkdir(parents=True)
    (tmp_path / "app" / "controllers" / "widgets_controller.rb").write_text(
        "class WidgetsController < ApplicationController\nend\n"
    )
    (tmp_path / "plugins" / "a" / "app" / "controllers" / "widgets_controller.rb").write_text(
        "class WidgetsController < ApplicationController\nend\n"
    )
    modules = [
        _module("app/controllers/widgets_controller.rb"),
        _module("plugins/a/app/controllers/widgets_controller.rb"),
    ]
    api_endpoints = [
        {
            "framework": "rails",
            "handler": "resources(...)",
            "path": "widgets",
            "unresolved": True,
            "file": "config/routes.rb",
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    unreachable_paths = {m["path"] for m in result["unreachable_modules"]}
    assert "app/controllers/widgets_controller.rb" in unreachable_paths
    assert "plugins/a/app/controllers/widgets_controller.rb" in unreachable_paths


def test_laravel_legacy_string_handler_controller_is_never_unreachable(tmp_path):
    # Real gap: the legacy "'UserController@index'" route-handler string
    # has no accompanying `use` import anywhere in the file (unlike the
    # newer [Controller::class, 'method'] array form, which does and is
    # already resolved correctly by the ordinary PHP import graph).
    controllers_dir = tmp_path / "app" / "Http" / "Controllers"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "UserController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\n\nclass UserController {\n"
        "    public function index() { return []; }\n}\n"
    )
    modules = [
        _module("app/Http/Controllers/UserController.php"),
        _module("routes/web.php"),
    ]
    api_endpoints = [
        {
            "framework": "laravel",
            "handler": "UserController@index",
            "path": "/users",
            "unresolved": False,
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert "app/Http/Controllers/UserController.php" not in [
        m["path"] for m in result["unreachable_modules"]
    ]


def test_laravel_array_class_handler_method_name_is_not_mistaken_for_a_controller(tmp_path):
    # [Controller::class, 'method'] entries record just the method name
    # ("index") as their handler, per _laravel_handler_label - this must
    # not be matched by the legacy "Controller@method" resolver (it has no
    # "@"), since that array form's controller reference is already a real
    # import the ordinary PHP import graph resolves on its own.
    modules = [_module("app/Http/Controllers/UserController.php")]
    api_endpoints = [
        {"framework": "laravel", "handler": "index", "path": "/users", "unresolved": False},
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert "app/Http/Controllers/UserController.php" in [
        m["path"] for m in result["unreachable_modules"]
    ]


def test_rails_namespaced_and_top_level_same_named_controllers_both_resolve(tmp_path):
    # Follow-up to test_rails_ambiguous_controller_basename_is_left_unresolved
    # above: once endpoints.py's Rails extractor tracks namespace/scope
    # module prefixes (see _rails_enclosing_module_prefix), the two real
    # "badges" entries carry distinct scoped names ("badges" vs
    # "admin/badges") instead of colliding, so both controllers resolve
    # correctly instead of both being left unreachable.
    (tmp_path / "app" / "controllers" / "admin").mkdir(parents=True)
    (tmp_path / "app" / "controllers" / "badges_controller.rb").write_text(
        "class BadgesController < ApplicationController\nend\n"
    )
    (tmp_path / "app" / "controllers" / "admin" / "badges_controller.rb").write_text(
        "class Admin::BadgesController < Admin::AdminController\nend\n"
    )
    modules = [
        _module("app/controllers/badges_controller.rb"),
        _module("app/controllers/admin/badges_controller.rb"),
    ]
    api_endpoints = [
        {
            "framework": "rails",
            "handler": "resources(...)",
            "path": "badges",
            "unresolved": True,
            "file": "config/routes.rb",
        },
        {
            "framework": "rails",
            "handler": "resources(...)",
            "path": "admin/badges",
            "unresolved": True,
            "file": "config/routes.rb",
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert result["unreachable_modules"] == []


def test_rails_unprefixed_resource_in_a_plugin_engine_routes_file_still_resolves(tmp_path):
    # Real regression caught by a real end-to-end check against Discourse:
    # a plugin's own routes file (mounted via `SomeEngine.routes.draw do
    # ... end`, not config/routes.rb) implicitly namespaces every
    # controller under the engine's own module (isolate_namespace) even
    # for an unprefixed `resources :workflows` - so the real controller
    # lives at plugins/.../app/controllers/discourse_workflows/
    # workflows_controller.rb, one level deeper than an unprefixed
    # config/routes.rb resource would ever be. Anchoring an unprefixed
    # name to "must be a direct child of controllers/" (correct for
    # config/routes.rb - see the namespace-collision test above) would
    # wrongly leave this real, uniquely-identified controller unresolved.
    controllers_dir = tmp_path / "plugins" / "discourse-workflows" / "app" / "controllers" / "discourse_workflows"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "workflows_controller.rb").write_text(
        "class DiscourseWorkflows::WorkflowsController < DiscourseWorkflows::AdminController\nend\n"
    )
    modules = [
        _module(
            "plugins/discourse-workflows/app/controllers/discourse_workflows/workflows_controller.rb"
        ),
    ]
    api_endpoints = [
        {
            "framework": "rails",
            "handler": "resources(...)",
            "path": "workflows",
            "unresolved": True,
            "file": "plugins/discourse-workflows/config/routes.rb",
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert result["unreachable_modules"] == []


def test_rails_model_referenced_only_by_bare_constant_is_never_unreachable(tmp_path):
    # Real bug found via a real Discourse scan: Rails' Zeitwerk autoloader
    # (default since Rails 6) means app/ code outside controllers is never
    # require'd anywhere at all - it's referenced purely by bare constant
    # name (User.find(...), never `require "user"`). rails_route_
    # reachable_files only ever covers controllers dispatched from
    # routes.rb; a model has no routes.rb entry to be rescued by at all.
    # Every one of Discourse's 391 app/models files was flagged dead
    # before this fix - not an edge case, the single most fundamental
    # Rails convention there is.
    models_dir = tmp_path / "app" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "user.rb").write_text("class User < ApplicationRecord\nend\n")
    controllers_dir = tmp_path / "app" / "controllers"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "sessions_controller.rb").write_text(
        "class SessionsController < ApplicationController\n"
        "  def show\n    @user = User.find(params[:id])\n  end\nend\n"
    )
    modules = [
        _module("app/models/user.rb"),
        _module("app/controllers/sessions_controller.rb", imported_by=["app/controllers/sessions_controller.rb"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_rails_namespaced_file_referenced_by_bare_last_segment_resolves(tmp_path):
    # Ruby's own lexical constant lookup lets code refer to a namespaced
    # class by its short name alone once already inside (or alongside) the
    # same namespace - real code commonly does this rather than always
    # spelling out the full qualified name.
    jobs_dir = tmp_path / "app" / "jobs" / "regular"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "bump_topic.rb").write_text(
        "module Jobs\n  class BumpTopic < Jobs::Base\n  end\nend\n"
    )
    other_dir = tmp_path / "lib"
    other_dir.mkdir(parents=True)
    (other_dir / "topic_bumper.rb").write_text(
        "class TopicBumper\n  def bump\n    BumpTopic.new.execute({})\n  end\nend\n"
    )
    modules = [
        _module("app/jobs/regular/bump_topic.rb"),
        _module("lib/topic_bumper.rb", imported_by=["lib/topic_bumper.rb"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_rails_job_dispatched_only_by_symbol_name_resolves(tmp_path):
    # Real gap found via audit against a real repo (discourse/discourse):
    # a custom job-dispatch layer (`Jobs.enqueue(:bump_topic, ...)`) built
    # on top of Zeitwerk's own autoloading, common enough to be worth
    # closing generally (any `dispatch(:snake_case_name)`-shaped call,
    # not hardcoded to any one method name) - 87 of Discourse's 241
    # app/jobs files were rescued by this alone, beyond what a bare
    # constant reference check already caught.
    jobs_dir = tmp_path / "app" / "jobs" / "regular"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "bump_topic.rb").write_text(
        "module Jobs\n  class BumpTopic < Jobs::Base\n  end\nend\n"
    )
    caller_dir = tmp_path / "app" / "models"
    caller_dir.mkdir(parents=True)
    (caller_dir / "topic.rb").write_text(
        "class Topic < ApplicationRecord\n"
        "  def bump\n    Jobs.enqueue(:bump_topic, topic_id: id)\n  end\nend\n"
    )
    modules = [
        _module("app/jobs/regular/bump_topic.rb"),
        _module("app/models/topic.rb", imported_by=["app/models/topic.rb"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_symbol_dispatch_does_not_match_a_keyword_argument_label(tmp_path):
    # A `key:` keyword-argument label (colon trailing the identifier) must
    # never be mistaken for a `:key` symbol literal (colon leading it) -
    # they look similar but mean something completely different in Ruby,
    # and the former is far more common in ordinary code that has nothing
    # to do with dispatch.
    jobs_dir = tmp_path / "app" / "jobs" / "regular"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "bump_topic.rb").write_text(
        "module Jobs\n  class BumpTopic < Jobs::Base\n  end\nend\n"
    )
    caller_dir = tmp_path / "app" / "models"
    caller_dir.mkdir(parents=True)
    (caller_dir / "topic.rb").write_text(
        "class Topic < ApplicationRecord\n"
        "  def some_unrelated_method(bump_topic: false)\n    bump_topic\n  end\nend\n"
    )
    modules = [
        _module("app/jobs/regular/bump_topic.rb"),
        _module("app/models/topic.rb", imported_by=["app/models/topic.rb"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert [m["path"] for m in result["unreachable_modules"]] == ["app/jobs/regular/bump_topic.rb"]


def test_rake_task_dispatching_a_job_by_symbol_resolves_it(tmp_path):
    # Real gap found via audit: .rake files are real Ruby (Rake tasks
    # routinely dispatch a job, e.g. lib/tasks/nested_replies.rake in
    # discourse/discourse) but aren't part of the module graph at all -
    # invisible to the rescue passes above unless read explicitly.
    jobs_dir = tmp_path / "app" / "jobs" / "regular"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "prepare_stats.rb").write_text(
        "module Jobs\n  class PrepareStats < Jobs::Base\n  end\nend\n"
    )
    tasks_dir = tmp_path / "lib" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "stats.rake").write_text(
        'task "stats:prepare" => :environment do\n'
        "  Jobs.enqueue(:prepare_stats)\nend\n"
    )
    modules = [_module("app/jobs/regular/prepare_stats.rb")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_leading_double_colon_superclass_reference_resolves(tmp_path):
    # Real bug found via a real Discourse scan: a top-level-anchored
    # constant reference (`::Admin::UserHistory` - Ruby's own syntax for
    # "start lookup at the absolute top-level namespace", commonly used
    # for a superclass reference to disambiguate against a same-named
    # nested constant) was completely invisible to the constant-token
    # index - not just imprecisely captured, but missed entirely,
    # including for the bare last-segment fallback, since the leading
    # `::` blocked a match from starting anywhere inside the reference
    # (confirmed directly: discourse/discourse's own
    # `class CloseTopic < ::Jobs::TopicTimerBase` never registered at
    # all before this fix).
    base_dir = tmp_path / "app" / "models" / "admin"
    base_dir.mkdir(parents=True)
    (base_dir / "user_history.rb").write_text(
        "module Admin\n  class UserHistory < ApplicationRecord\n  end\nend\n"
    )
    subclass_dir = tmp_path / "app" / "models" / "admin"
    (subclass_dir / "staff_action_log.rb").write_text(
        "module Admin\n  class StaffActionLog < ::Admin::UserHistory\n  end\nend\n"
    )
    modules = [
        _module("app/models/admin/user_history.rb"),
        _module("app/models/admin/staff_action_log.rb", imported_by=["app/models/admin/staff_action_log.rb"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_rails_model_with_no_reference_anywhere_stays_unreachable(tmp_path):
    # The rescue must not become a rescue-everything hack - a genuinely
    # orphaned model, referenced nowhere in the corpus, still gets flagged.
    models_dir = tmp_path / "app" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "abandoned_draft.rb").write_text(
        "class AbandonedDraft < ApplicationRecord\nend\n"
    )
    modules = [_module("app/models/abandoned_draft.rb")]
    result = find_dead_code(tmp_path, modules, config=None)
    assert [m["path"] for m in result["unreachable_modules"]] == ["app/models/abandoned_draft.rb"]


def test_ruby_constant_qualified_off_a_lowercase_expression_does_not_match(tmp_path):
    # Flash Review finding: `registry::User` is valid Ruby (:: after any
    # expression that evaluates to a module/class, not just a literal
    # constant chain) and refers to whatever "User" is nested under
    # registry's return value - NOT the top-level Zeitwerk `User`. The
    # lookbehind alone can't tell "::" preceded by a real absolute-
    # reference boundary from "::" preceded by a lowercase expression;
    # only the latter must be rejected.
    models_dir = tmp_path / "app" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "user.rb").write_text("class User < ApplicationRecord\nend\n")
    other_dir = tmp_path / "lib"
    other_dir.mkdir(parents=True)
    (other_dir / "consumer.rb").write_text(
        "class Consumer\n  def call\n    registry::User.new\n  end\nend\n"
    )
    modules = [
        _module("app/models/user.rb"),
        _module("lib/consumer.rb", imported_by=["lib/consumer.rb"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert [m["path"] for m in result["unreachable_modules"]] == ["app/models/user.rb"]


def test_ruby_top_level_anchored_constant_reference_still_resolves(tmp_path):
    # Must not regress while fixing the case above: a genuine absolute
    # top-level reference (Ruby's own "start lookup at the top-level
    # namespace" syntax, real and common for a superclass reference that
    # disambiguates against a same-named nested constant - confirmed on
    # Discourse's own `class CloseTopic < ::Jobs::TopicTimerBase`) must
    # still resolve.
    #
    # Path chosen so the referenced file's own Zeitwerk-derived full name
    # (segments after app/) is exactly "Jobs::TopicTimerBase", matching
    # the reference verbatim - this isolates the regex fix itself (does a
    # leading `::` reference still get indexed and matched at all) from
    # a separate, real gap this PR doesn't address: Discourse's actual
    # app/jobs root is registered with a custom Zeitwerk namespace
    # (`push_dir(..., namespace: Jobs)`), which _ruby_zeitwerk_constant_
    # candidates has no way to see from a plain file path - confirmed
    # directly against the real repo, discourse/discourse's own
    # app/jobs/regular/topic_timer_base.rb (candidates "Regular::
    # TopicTimerBase"/"TopicTimerBase") still doesn't match a real
    # "Jobs::TopicTimerBase" reference and stays unreachable even with
    # this fix applied - a distinct, deeper limitation than what either
    # Flash Review finding here asked for.
    jobs_dir = tmp_path / "app" / "jobs" / "jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "topic_timer_base.rb").write_text(
        "module Jobs\n  class TopicTimerBase < Jobs::Base\n  end\nend\n"
    )
    other_dir = tmp_path / "app" / "jobs" / "regular"
    other_dir.mkdir(parents=True)
    (other_dir / "close_topic.rb").write_text(
        "class CloseTopic < ::Jobs::TopicTimerBase\nend\n"
    )
    modules = [
        _module("app/jobs/jobs/topic_timer_base.rb"),
        _module(
            "app/jobs/regular/close_topic.rb",
            imported_by=["app/jobs/regular/close_topic.rb"],
        ),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert "app/jobs/jobs/topic_timer_base.rb" not in [
        m["path"] for m in result["unreachable_modules"]
    ]


def test_ruby_symbol_as_hash_value_does_not_dispatch(tmp_path):
    # Flash Review finding: the symbol-dispatch index matched ANY bare
    # symbol literal anywhere in the source, not just one in a real
    # dispatch-call position - an ordinary `{status: :active}` hash value,
    # an `enum status: [:active, :inactive]` declaration, or any other
    # bare `:some_word` used as plain data would register exactly like a
    # real `Jobs.enqueue(:bump_topic, ...)` call.
    jobs_dir = tmp_path / "app" / "jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "active.rb").write_text("class Active < ApplicationRecord\nend\n")
    other_dir = tmp_path / "lib"
    other_dir.mkdir(parents=True)
    (other_dir / "widget.rb").write_text(
        "class Widget\n  enum status: [:active, :inactive]\n"
        "  def describe\n    {status: :active}\n  end\nend\n"
    )
    modules = [
        _module("app/jobs/active.rb"),
        _module("lib/widget.rb", imported_by=["lib/widget.rb"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert [m["path"] for m in result["unreachable_modules"]] == ["app/jobs/active.rb"]


def test_ruby_symbol_dispatch_call_still_resolves(tmp_path):
    # Must not regress while fixing the case above: a genuine dispatch
    # call (confirmed on Discourse's own `Jobs.enqueue(:bump_topic, ...)`)
    # must still resolve, including across a multi-line call.
    jobs_dir = tmp_path / "app" / "jobs" / "regular"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "bump_topic.rb").write_text(
        "module Jobs\n  class BumpTopic < Jobs::Base\n  end\nend\n"
    )
    other_dir = tmp_path / "lib"
    other_dir.mkdir(parents=True)
    (other_dir / "bumper.rb").write_text(
        "class Bumper\n  def call\n"
        "    Jobs.enqueue(\n      :bump_topic,\n      topic_id: 1\n    )\n"
        "  end\nend\n"
    )
    modules = [
        _module("app/jobs/regular/bump_topic.rb"),
        _module("lib/bumper.rb", imported_by=["lib/bumper.rb"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert result["unreachable_modules"] == []


def test_ruby_zeitwerk_rescue_is_scoped_to_app_directory_only(tmp_path):
    # lib/ is only Zeitwerk-autoloaded when an app explicitly opts in via
    # config.autoload_paths, which this module has no way to see - so a
    # lib/ file must NOT be rescued by this mechanism even when its own
    # class name happens to appear as a bare word elsewhere (it should
    # still be resolved some other way - a real `require`, or left dead -
    # never guessed via the app/-only Zeitwerk convention).
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir(parents=True)
    (lib_dir / "helper_util.rb").write_text("class HelperUtil\nend\n")
    other_dir = tmp_path / "app" / "models"
    other_dir.mkdir(parents=True)
    (other_dir / "unrelated.rb").write_text(
        "class Unrelated\n  def call\n    HelperUtil.new\n  end\nend\n"
    )
    modules = [
        _module("lib/helper_util.rb"),
        _module("app/models/unrelated.rb", imported_by=["app/models/unrelated.rb"]),
    ]
    result = find_dead_code(tmp_path, modules, config=None)
    assert [m["path"] for m in result["unreachable_modules"]] == ["lib/helper_util.rb"]


def test_laravel_backslash_qualified_string_handler_resolves_a_nested_controller(tmp_path):
    # Flash Review finding on #666: the legacy string handler resolver
    # reduced every handler to its bare class name and always anchored
    # single-segment queries to a controllers/-directory boundary, so a
    # real, fully-qualified handler like "Admin\UserController@index"
    # naming a controller under app/Http/Controllers/Admin/ was wrongly
    # left flagged as dead code - anchoring assumes an unqualified name
    # means "top level", which doesn't apply here since the handler
    # string already names its own namespace segment.
    controllers_dir = tmp_path / "app" / "Http" / "Controllers" / "Admin"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "UserController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers\\Admin;\n\nclass UserController {\n"
        "    public function index() { return []; }\n}\n"
    )
    modules = [_module("app/Http/Controllers/Admin/UserController.php")]
    api_endpoints = [
        {
            "framework": "laravel",
            "handler": "Admin\\UserController@index",
            "path": "/admin/users",
            "unresolved": False,
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert result["unreachable_modules"] == []


def test_laravel_route_resource_controller_is_never_unreachable(tmp_path):
    # Real gap found via audit: Route::resource()/apiResource() - Laravel's
    # own standard CRUD-controller idiom, direct equivalent of Rails'
    # `resources` - was completely unextracted, so any controller wired up
    # this way (arguably Laravel's single most common controller pattern)
    # was always flagged dead code.
    controllers_dir = tmp_path / "app" / "Http" / "Controllers"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "UserController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\n\nclass UserController {\n"
        "    public function index() { return []; }\n}\n"
    )
    modules = [_module("app/Http/Controllers/UserController.php")]
    api_endpoints = [
        {
            "framework": "laravel",
            "handler": "UserController",
            "path": "users",
            "unresolved": True,
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert result["unreachable_modules"] == []


def test_laravel_route_resource_namespaced_controller_resolves_uniquely(tmp_path):
    controllers_dir = tmp_path / "app" / "Http" / "Controllers" / "Admin"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "UserController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers\\Admin;\n\nclass UserController {}\n"
    )
    other_dir = tmp_path / "app" / "Http" / "Controllers"
    (other_dir / "UserController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\n\nclass UserController {}\n"
    )
    modules = [
        _module("app/Http/Controllers/Admin/UserController.php"),
        _module("app/Http/Controllers/UserController.php"),
    ]
    api_endpoints = [
        {
            "framework": "laravel",
            "handler": "Admin\\UserController",
            "path": "admin/users",
            "unresolved": True,
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    unreachable_paths = {m["path"] for m in result["unreachable_modules"]}
    # The namespaced one resolves via its full qualified name...
    assert "app/Http/Controllers/Admin/UserController.php" not in unreachable_paths
    # ...while the unrelated top-level one, never named by any route here,
    # correctly stays flagged.
    assert "app/Http/Controllers/UserController.php" in unreachable_paths


def test_laravel_leading_backslash_fully_qualified_class_ref_resolves(tmp_path):
    # Real bug found via peer review of this PR: `\App\Http\Controllers\
    # UserController::class` (a valid, fairly common fully-root-qualified
    # PHP class reference) parses with the leading backslash included in
    # its text span. Splitting that on "\\" produces a leading empty
    # string, which derails every segment after it and never matches any
    # real path - reproduced end-to-end before the fix, both for this
    # resolver's `unresolved` (Route::resource()) branch here and for the
    # pre-existing legacy string-handler branch below, which shared the
    # same bug since #666.
    controllers_dir = tmp_path / "app" / "Http" / "Controllers"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "UserController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\n\nclass UserController {}\n"
    )
    modules = [_module("app/Http/Controllers/UserController.php")]
    api_endpoints = [
        {
            "framework": "laravel",
            "handler": "\\App\\Http\\Controllers\\UserController",
            "path": "users",
            "unresolved": True,
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert result["unreachable_modules"] == []


def test_laravel_leading_backslash_legacy_string_handler_resolves(tmp_path):
    # Same bug, the pre-existing (#666) legacy string-handler branch.
    controllers_dir = tmp_path / "app" / "Http" / "Controllers"
    controllers_dir.mkdir(parents=True)
    (controllers_dir / "UserController.php").write_text(
        "<?php\nnamespace App\\Http\\Controllers;\n\nclass UserController {\n"
        "    public function index() {}\n}\n"
    )
    modules = [_module("app/Http/Controllers/UserController.php")]
    api_endpoints = [
        {
            "framework": "laravel",
            "handler": "\\App\\Http\\Controllers\\UserController@index",
            "path": "/users",
            "unresolved": False,
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert result["unreachable_modules"] == []


def test_rails_resources_controller_override_resolves_the_real_controller(tmp_path):
    # End-to-end version of the config/routes.rb:356 Discourse bug: without
    # honoring the `controller:` override, dead_code.py would look for
    # (and fail to find) a nonexistent keys_controller.rb instead of the
    # real api_controller.rb.
    (tmp_path / "app" / "controllers").mkdir(parents=True)
    (tmp_path / "app" / "controllers" / "api_controller.rb").write_text(
        "class ApiController < ApplicationController\nend\n"
    )
    modules = [_module("app/controllers/api_controller.rb")]
    api_endpoints = [
        {
            "framework": "rails",
            "handler": "resources(...)",
            "path": "api",
            "unresolved": True,
            "file": "config/routes.rb",
        },
    ]
    result = find_dead_code(tmp_path, modules, config=None, api_endpoints=api_endpoints)
    assert result["unreachable_modules"] == []
