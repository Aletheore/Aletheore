from pathlib import Path

from aletheore.scanner.graph import build_module_graph
from conftest import symbol_names


def make_kotlin_repo(tmp_path: Path) -> Path:
    # Mirrors a real project shape verified by actually parsing it with the
    # real tree-sitter-kotlin grammar before this fixture was written (see
    # the throwaway AST-probe scripts used to build _extract_kotlin).
    # data/TaskRepository.kt deliberately exports two top-level
    # declarations (TasksRepository and DefaultTasksRepository) neither of
    # which matches the file's own name - idiomatic and common in real
    # Kotlin (confirmed against android/architecture-samples), and the one
    # real gap Java-style same-name-as-file resolution can't handle at all.
    repo = tmp_path / "repo"
    src = repo / "src" / "main" / "kotlin" / "com" / "example" / "app"
    (src / "data").mkdir(parents=True)

    (src / "data" / "TaskRepository.kt").write_text(
        "package com.example.app.data\n\n"
        "const val MAX_TASKS = 100\n"
        "private const val TAG = \"TasksRepo\"\n\n"
        "class TasksRepository {\n"
        "    fun getTasks(): List<String> = emptyList()\n\n"
        "    private fun helper() {}\n\n"
        "    internal fun internalOnly() {}\n"
        "}\n\n"
        "data class Task(val id: String, val title: String)\n\n"
        "interface TaskRepository {\n"
        "    fun getTask(id: String): Task?\n"
        "}\n\n"
        "object DefaultTasksRepository {\n"
        "    fun create(): TasksRepository = TasksRepository()\n"
        "}\n\n"
        "enum class Priority { LOW, MEDIUM, HIGH }\n"
    )
    (src / "Main.kt").write_text(
        "package com.example.app\n\n"
        "import com.example.app.data.TasksRepository\n"
        "import com.example.app.data.DefaultTasksRepository\n\n"
        "fun main() {\n"
        "    val repo = DefaultTasksRepository.create()\n"
        "    repo.getTasks()\n"
        "}\n"
    )
    return repo


def test_build_module_graph_extracts_kotlin_symbols(tmp_path):
    repo = make_kotlin_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    task_repo_file = by_path["src/main/kotlin/com/example/app/data/TaskRepository.kt"]
    assert task_repo_file["language"] == "kotlin"

    class_names = symbol_names(task_repo_file["symbols"]["classes"])
    assert "TasksRepository" in class_names  # class
    assert "Task" in class_names  # data class
    assert "TaskRepository" in class_names  # interface
    assert "DefaultTasksRepository" in class_names  # object
    assert "Priority" in class_names  # enum class

    fn_names = set(symbol_names(task_repo_file["symbols"]["functions"]))
    assert {"getTasks", "helper", "internalOnly", "getTask", "create"} <= fn_names

    get_tasks_fn = next(f for f in task_repo_file["symbols"]["functions"] if f["name"] == "getTasks")
    assert get_tasks_fn["params"] == "()"
    assert get_tasks_fn["return_type"] == "List<String>"

    interface_symbol = next(c for c in task_repo_file["symbols"]["classes"] if c["name"] == "TaskRepository")
    assert interface_symbol["is_pure_declaration"] is True
    class_symbol = next(c for c in task_repo_file["symbols"]["classes"] if c["name"] == "TasksRepository")
    assert class_symbol["is_pure_declaration"] is False

    constant_names = set(symbol_names(task_repo_file["symbols"]["constants"]))
    assert constant_names == {"MAX_TASKS", "TAG"}

    assert unparseable == []


def test_kotlin_default_visibility_is_public_unlike_java(tmp_path):
    # The one Java assumption that would be silently wrong if copied
    # verbatim: Java's package-private default means "no modifier ->
    # private" in _java_is_public's absent-modifier branches, but Kotlin's
    # default is the opposite - a declaration with no visibility modifier
    # at all is fully public. Explicit private/internal are the only two
    # ways to NOT be public here.
    repo = make_kotlin_repo(tmp_path)
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    task_repo_file = by_path["src/main/kotlin/com/example/app/data/TaskRepository.kt"]

    functions_by_name = {f["name"]: f for f in task_repo_file["symbols"]["functions"]}
    assert functions_by_name["getTasks"]["is_public"] is True  # no modifier at all
    assert functions_by_name["helper"]["is_public"] is False  # private
    assert functions_by_name["internalOnly"]["is_public"] is False  # internal

    constants_by_name = {c["name"]: c for c in task_repo_file["symbols"]["constants"]}
    assert constants_by_name["MAX_TASKS"]["is_public"] is True  # const val, no modifier
    assert constants_by_name["TAG"]["is_public"] is False  # private const val

    classes_by_name = {c["name"]: c for c in task_repo_file["symbols"]["classes"]}
    assert classes_by_name["TasksRepository"]["is_public"] is True  # class, no modifier


def test_build_module_graph_kotlin_import_resolves_despite_filename_mismatch(tmp_path):
    # The real, Kotlin-specific import-resolution gap Java-style same-
    # name-as-file lookup can't handle: TaskRepository.kt exports
    # TasksRepository and DefaultTasksRepository, neither matching the
    # filename. _kotlin_class_file's content-search fallback is what
    # makes this resolve at all.
    repo = make_kotlin_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert (
        "src/main/kotlin/com/example/app/Main.kt",
        "src/main/kotlin/com/example/app/data/TaskRepository.kt",
    ) in edges


def test_build_module_graph_kotlin_top_level_function_import_resolves(tmp_path):
    # Real repo confirmed (android/architecture-samples): ComposeUtils.kt's
    # `fun LoadingContent(...)` (the dominant declaration shape in real
    # Jetpack Compose code) imported by name from three other files,
    # resolved to nothing before this - _kotlin_class_file's needle only
    # matched class/interface/object, never a top-level function.
    repo = tmp_path / "repo"
    src = repo / "src" / "main" / "kotlin" / "com" / "example"
    (src / "util").mkdir(parents=True)
    (src / "util" / "ComposeUtils.kt").write_text(
        "package com.example.util\n\n"
        "@Composable\n"
        "fun LoadingContent(loading: Boolean) {}\n"
    )
    (src / "Main.kt").write_text(
        "package com.example\n\n"
        "import com.example.util.LoadingContent\n\n"
        "fun main() { LoadingContent(true) }\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert (
        "src/main/kotlin/com/example/Main.kt",
        "src/main/kotlin/com/example/util/ComposeUtils.kt",
    ) in edges


def test_build_module_graph_kotlin_extension_function_import_resolves(tmp_path):
    # Real repo confirmed (android/architecture-samples): ModelMappingExt.kt's
    # `fun LocalTask.toExternal()` - an extension function, where the
    # imported name ("toExternal") comes after the receiver type
    # ("LocalTask."), not right after `fun`.
    repo = tmp_path / "repo"
    src = repo / "src" / "main" / "kotlin" / "com" / "example"
    (src / "data").mkdir(parents=True)
    (src / "data" / "ModelMappingExt.kt").write_text(
        "package com.example.data\n\n"
        "class LocalTask(val id: String)\n"
        "class Task(val id: String)\n\n"
        "fun LocalTask.toExternal() = Task(id)\n"
    )
    (src / "Main.kt").write_text(
        "package com.example\n\n"
        "import com.example.data.LocalTask\n"
        "import com.example.data.toExternal\n\n"
        "fun main() { LocalTask(\"1\").toExternal() }\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert (
        "src/main/kotlin/com/example/Main.kt",
        "src/main/kotlin/com/example/data/ModelMappingExt.kt",
    ) in edges


def test_build_module_graph_kotlin_top_level_val_import_resolves(tmp_path):
    # Real repo confirmed (android/architecture-samples): CoroutinesUtils.kt's
    # `val WhileUiSubscribed: SharingStarted = ...` - a module-level
    # constant/computed property, imported by name from another file, had
    # zero incoming edges before this (needle only matched class/interface/
    # object/fun, never val/var).
    repo = tmp_path / "repo"
    src = repo / "src" / "main" / "kotlin" / "com" / "example"
    (src / "util").mkdir(parents=True)
    (src / "util" / "CoroutinesUtils.kt").write_text(
        "package com.example.util\n\n"
        "val WhileUiSubscribed: Long = 5000L\n"
    )
    (src / "Main.kt").write_text(
        "package com.example\n\n"
        "import com.example.util.WhileUiSubscribed\n\n"
        "fun main() { println(WhileUiSubscribed) }\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert (
        "src/main/kotlin/com/example/Main.kt",
        "src/main/kotlin/com/example/util/CoroutinesUtils.kt",
    ) in edges


def test_build_module_graph_kotlin_wildcard_import_resolves_every_file_in_package(tmp_path):
    repo = tmp_path / "repo"
    src = repo / "src" / "main" / "kotlin" / "com" / "example"
    (src / "data").mkdir(parents=True)
    (src / "data" / "A.kt").write_text("package com.example.data\n\nclass A\n")
    (src / "data" / "B.kt").write_text("package com.example.data\n\nclass B\n")
    (src / "Main.kt").write_text(
        "package com.example\n\nimport com.example.data.*\n\nfun main() {}\n"
    )

    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}

    assert ("src/main/kotlin/com/example/Main.kt", "src/main/kotlin/com/example/data/A.kt") in edges
    assert ("src/main/kotlin/com/example/Main.kt", "src/main/kotlin/com/example/data/B.kt") in edges


def test_build_module_graph_kotlin_unresolvable_import_does_not_crash(tmp_path):
    repo = tmp_path / "repo"
    src = repo / "src" / "main" / "kotlin" / "com" / "example"
    src.mkdir(parents=True)
    (src / "Main.kt").write_text(
        "package com.example\n\nimport kotlinx.coroutines.flow.Flow\n\nfun main() {}\n"
    )

    modules, dependency_graph, unparseable = build_module_graph(repo)

    assert unparseable == []
    assert dependency_graph["edges"] == []
    by_path = {m["path"]: m for m in modules}
    assert by_path["src/main/kotlin/com/example/Main.kt"]["language"] == "kotlin"


def test_build_module_graph_kotlin_kts_extension_uses_own_containing_directory(tmp_path):
    # A .kts script (build.gradle.kts, a Gradle Kotlin DSL file) has no
    # package_header at all - real, common, and different from every
    # ordinary .kt source file. _kotlin_source_root_for's `not package`
    # branch falls back to the file's own containing directory rather
    # than crashing or mis-inferring a package-shaped root.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.gradle.kts").write_text(
        "plugins {\n    kotlin(\"jvm\")\n}\n"
    )

    modules, dependency_graph, unparseable = build_module_graph(repo)

    assert unparseable == []
    by_path = {m["path"]: m for m in modules}
    assert by_path["build.gradle.kts"]["language"] == "kotlin"
