from pathlib import Path

import pytest

from aletheore.scanner.graph import MAX_SOURCE_FILE_BYTES, _is_public_symbol, build_module_graph
from conftest import symbol_names


def test_build_module_graph_records_oversized_source_without_parsing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "large.py").write_bytes(b"x = 1\n" + b"#" * MAX_SOURCE_FILE_BYTES)

    modules, _graph, unparseable = build_module_graph(repo)

    assert modules == []
    assert unparseable == [{"path": "large.py", "reason": "file exceeds size limit"}]


def make_python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    app = repo / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "config.py").write_text("SETTING = 1\n\ndef load():\n    return SETTING\n")
    (app / "auth.py").write_text(
        "from app import config\n\n\ndef login():\n    return config.load()\n\n\nclass AuthError(Exception):\n    pass\n"
    )
    (app / "routes.py").write_text("from app.auth import login\n\ndef handle():\n    return login()\n")
    return repo


def test_build_module_graph_extracts_python_imports_and_symbols(tmp_path):
    repo = make_python_repo(tmp_path)
    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    assert "app/auth.py" in by_path
    auth = by_path["app/auth.py"]
    assert "app/config.py" in auth["imports"]
    assert "login" in symbol_names(auth["symbols"]["functions"])
    assert "AuthError" in symbol_names(auth["symbols"]["classes"])

    config = by_path["app/config.py"]
    assert "app/auth.py" in config["imported_by"]

    assert unparseable == []


def test_build_module_graph_records_symbol_line_bounds(tmp_path):
    repo = make_python_repo(tmp_path)
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    auth = by_path["app/auth.py"]

    login_fn = next(f for f in auth["symbols"]["functions"] if f["name"] == "login")
    assert login_fn["start_line"] == 4
    assert login_fn["end_line"] == 5
    assert login_fn["params"] == "()"

    auth_error_cls = next(c for c in auth["symbols"]["classes"] if c["name"] == "AuthError")
    assert auth_error_cls["start_line"] == 8
    assert auth_error_cls["end_line"] == 9
    # Classes have no parameter list - unlike functions, params is always
    # None for them, not an empty string.
    assert auth_error_cls["params"] is None


def test_symbol_entry_always_includes_docstring_return_type_and_is_public_keys(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n")
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert set(func) == {
        "name", "start_line", "end_line", "params", "docstring", "return_type", "is_public",
        "is_pure_declaration",
    }
    assert func["docstring"] is None
    assert func["return_type"] is None
    assert func["is_public"] is True


def test_python_extracts_docstring_and_return_type(tmp_path):
    (tmp_path / "a.py").write_text(
        'def greet(name: str) -> str:\n    """Return a greeting."""\n    return f"hi {name}"\n'
    )
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Return a greeting."
    assert func["return_type"] == "str"


def test_python_function_with_no_docstring_or_annotation_gets_none(tmp_path):
    (tmp_path / "a.py").write_text("def f(x):\n    return x\n")
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] is None
    assert func["return_type"] is None


def test_python_class_docstring_is_extracted(tmp_path):
    (tmp_path / "a.py").write_text(
        'class Greeter:\n    """Greets people."""\n\n    def greet(self):\n        pass\n'
    )
    modules, _, _ = build_module_graph(tmp_path)
    cls = modules[0]["symbols"]["classes"][0]
    assert cls["docstring"] == "Greets people."


def test_python_first_statement_not_a_string_is_not_treated_as_docstring(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    x = 1\n    return x\n")
    modules, _, _ = build_module_graph(tmp_path)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] is None


@pytest.mark.parametrize("name,language,expected", [
    ("get_user", "python", True),
    ("_internal_helper", "python", False),
    ("__dunder__", "python", False),
    ("GetUser", "go", True),
    ("getUser", "go", False),
    ("getUser", "javascript", True),
    ("PublicMethod", "csharp", True),
    ("privateMethod", "csharp", True),
    ("save", "ruby", True),
    ("_looks_private_but_ruby_has_no_naming_convention", "ruby", True),
])
def test_is_public_symbol(name, language, expected):
    assert _is_public_symbol(name, language) is expected


def test_python_underscore_prefixed_function_is_marked_private(tmp_path):
    (tmp_path / "a.py").write_text("def _helper():\n    pass\n\ndef public_fn():\n    pass\n")
    modules, _, _ = build_module_graph(tmp_path)
    by_name = {f["name"]: f for f in modules[0]["symbols"]["functions"]}
    assert by_name["_helper"]["is_public"] is False
    assert by_name["public_fn"]["is_public"] is True


def test_python_closure_defined_inside_a_function_is_not_marked_public(tmp_path):
    # Caught via dogfooding `aletheore docs` against this repo's own scanner code:
    # nested helper functions like graph.py's own `walk`/`text` closures were being
    # extracted as if they were top-level public symbols.
    (tmp_path / "a.py").write_text(
        "def outer():\n    def inner():\n        pass\n    return inner\n\ndef top_level():\n    pass\n"
    )
    modules, _, _ = build_module_graph(tmp_path)
    by_name = {f["name"]: f for f in modules[0]["symbols"]["functions"]}
    assert by_name["inner"]["is_public"] is False
    assert by_name["outer"]["is_public"] is True
    assert by_name["top_level"]["is_public"] is True


def test_python_method_inside_a_class_is_not_treated_as_nested_in_a_function(tmp_path):
    (tmp_path / "a.py").write_text("class Widget:\n    def render(self):\n        pass\n")
    modules, _, _ = build_module_graph(tmp_path)
    method = modules[0]["symbols"]["functions"][0]
    assert method["is_public"] is True


def test_javascript_named_function_nested_in_an_arrow_function_is_not_public(tmp_path):
    # The gap the original nesting fix missed: _is_nested_in_function only
    # recognized named function/method ancestors. A named function whose
    # ONLY enclosing container is an anonymous arrow function (no named
    # function anywhere further up - e.g. `const outer = () => { ... }`,
    # or the extremely common `useEffect(() => { function handler(){} })`
    # pattern) had no matching ancestor at all and was still marked public.
    (tmp_path / "a.js").write_text(
        "const outer = () => {\n  function inner() { return 1; }\n};\n\nfunction topLevel() {}\n"
    )
    modules, _, _ = build_module_graph(tmp_path)
    by_name = {f["name"]: f for f in modules[0]["symbols"]["functions"]}
    assert by_name["inner"]["is_public"] is False
    assert by_name["topLevel"]["is_public"] is True


def test_javascript_class_nested_in_a_function_expression_is_not_public(tmp_path):
    (tmp_path / "a.js").write_text(
        "const factory = function() {\n  class Local {}\n  return Local;\n};\n"
    )
    modules, _, _ = build_module_graph(tmp_path)
    cls = modules[0]["symbols"]["classes"][0]
    assert cls["is_public"] is False


def test_build_module_graph_normalizes_multiline_function_signatures(tmp_path):
    # A signature reformatted across multiple lines (wrapped for length,
    # extra indentation) must diff identically to its single-line
    # equivalent - this is what makes "did the signature actually change"
    # detection resilient to pure formatting changes.
    repo = tmp_path / "repo"
    app = repo / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "billing.py").write_text(
        "def get_billing(\n"
        "    user_id: int,\n"
        "    include_history: bool = False,\n"
        ") -> dict:\n"
        "    return {}\n"
    )
    modules, _, _ = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    billing_fn = next(f for f in by_path["app/billing.py"]["symbols"]["functions"] if f["name"] == "get_billing")
    assert billing_fn["params"] == "( user_id: int, include_history: bool = False, )"


def test_build_module_graph_dependency_edges(tmp_path):
    repo = make_python_repo(tmp_path)
    _, dependency_graph, _ = build_module_graph(repo)
    edges = {tuple(edge) for edge in dependency_graph["edges"]}
    assert ("app/auth.py", "app/config.py") in edges
    assert ("app/routes.py", "app/auth.py") in edges


def test_build_module_graph_records_unparseable_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "helper.swift").write_text("func hi() {}\n")
    modules, _, unparseable = build_module_graph(repo)
    assert modules == []
    assert unparseable == [{"path": "helper.swift", "reason": "no grammar registered for .swift"}]


def test_build_module_graph_extracts_javascript_imports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "utils.js").write_text("export function add(a, b) { return a + b; }\n")
    (repo / "index.js").write_text(
        "import { add } from './utils';\n\nfunction main() { return add(1, 2); }\n"
    )
    modules, dependency_graph, unparseable = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    assert "index.js" in by_path
    assert "utils.js" in by_path["index.js"]["imports"]
    add_fn = next(f for f in by_path["utils.js"]["symbols"]["functions"] if f["name"] == "add")
    assert add_fn["params"] == "(a, b)"
    assert unparseable == []


def test_build_module_graph_extracts_commonjs_reexports_and_dynamic_imports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "foo.js").write_text("export const foo = 1;\n")
    (repo / "baz.js").write_text("export const baz = 2;\n")
    (repo / "qux.js").write_text("export const qux = 3;\n")
    (repo / "consumer.js").write_text(
        "const foo = require('./foo');\n"
        "export { baz } from './baz';\n"
        "export * from './qux';\n"
        "async function load() { return import('./foo'); }\n"
    )

    modules, dependency_graph, unparseable = build_module_graph(repo)
    consumer = next(module for module in modules if module["path"] == "consumer.js")

    assert consumer["imports"] == ["foo.js", "baz.js", "qux.js", "foo.js"]
    assert ["consumer.js", "foo.js"] in dependency_graph["edges"]
    assert ["consumer.js", "baz.js"] in dependency_graph["edges"]
    assert ["consumer.js", "qux.js"] in dependency_graph["edges"]
    assert unparseable == []


def test_javascript_extracts_jsdoc_comment(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.js").write_text(
        "/**\n * Adds two numbers.\n */\nfunction add(a, b) {\n  return a + b;\n}\n"
    )
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers."


def test_javascript_extracts_jsdoc_comment_on_exported_function(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.js").write_text(
        "/**\n * Adds two numbers.\n */\nexport function add(a, b) {\n  return a + b;\n}\n"
    )
    modules, _, _ = build_module_graph(repo)
    func = modules[0]["symbols"]["functions"][0]
    assert func["docstring"] == "Adds two numbers."


def test_javascript_function_with_no_leading_comment_gets_none(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.js").write_text("function add(a, b) {\n  return a + b;\n}\n")
    modules, _, _ = build_module_graph(repo)
    assert modules[0]["symbols"]["functions"][0]["docstring"] is None


def test_javascript_plain_line_comment_is_not_treated_as_jsdoc(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.js").write_text("// just a note\nfunction add(a, b) {\n  return a + b;\n}\n")
    modules, _, _ = build_module_graph(repo)
    assert modules[0]["symbols"]["functions"][0]["docstring"] is None


def test_typescript_extracts_return_type(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.ts").write_text("function add(a: number, b: number): number {\n  return a + b;\n}\n")
    modules, _, _ = build_module_graph(repo)
    assert modules[0]["symbols"]["functions"][0]["return_type"] == "number"


def test_build_module_graph_skips_non_source_files_silently(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    (repo / "data.json").write_text("{}")
    (repo / "logo.png").write_bytes(b"\x89PNG")
    (repo / "notes.md").write_text("# hi")
    modules, _, unparseable = build_module_graph(repo)
    assert unparseable == []
    assert {m["path"] for m in modules} == {"main.py"}


def test_build_module_graph_ignores_cache_and_build_dirs(tmp_path):
    repo = tmp_path / "repo"
    cache = repo / ".mypy_cache" / "3.12"
    cache.mkdir(parents=True)
    (cache / "module.data.json").write_text("{}")
    (repo / "dist").mkdir()
    (repo / "dist" / "bundle.js").write_text("console.log(1)")
    (repo / "main.py").write_text("x = 1\n")
    modules, _, unparseable = build_module_graph(repo)
    assert unparseable == []
    assert {m["path"] for m in modules} == {"main.py"}


def test_build_module_graph_ignores_nested_git_worktree(tmp_path):
    # Same real-world bug as test_detect.py's version of this test: a linked git
    # worktree is a directory (any name) containing its own `.git` file, and its
    # contents duplicated every real module in a live scan (confirmed: 203 of 492
    # modules were `.claude/worktrees/<name>/`-prefixed duplicates of real files).
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    worktree = repo / "some-custom-worktree-name"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/some-custom-worktree-name\n")
    (worktree / "main.py").write_text("x = 1\ny = 2\n")

    modules, _, unparseable = build_module_graph(repo)
    assert unparseable == []
    assert {m["path"] for m in modules} == {"main.py"}


def test_python_source_roots_returns_a_sorted_deterministic_list(tmp_path):
    # Built via a set (roots.add(...) while walking the tree), which iterates
    # in an order that depends on Path hashing - stable within one process
    # but not guaranteed across separate interpreter runs (PYTHONHASHSEED is
    # randomized per-process by default). Confirmed empirically: 12 separate
    # `aletheore scan` invocations of the same repo split 6/6 between two
    # different absolute-import resolutions. _resolve_python_module tries
    # roots in order and returns on the first match, so an unsorted roots
    # list makes an ambiguous import (resolvable via more than one root)
    # resolve differently run to run. Sorting can't know which root a real
    # `python` interpreter would actually prefer, but it does make the same
    # repo always produce the same answer.
    from aletheore.scanner.graph import _python_source_roots

    repo = tmp_path / "repo"
    for project, module in (("src", "pkg"), ("app", "pkg2")):
        pkg_dir = repo / project / module
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "mod.py").write_text("x = 1\n")

    roots = _python_source_roots(repo)

    assert roots == sorted(roots, key=lambda p: (len(p.parts), str(p)))
    assert roots == [repo, repo / "app", repo / "src"]


def test_build_module_graph_import_resolution_ignores_nested_git_worktree(tmp_path):
    # A different layer of the same real bug as
    # test_build_module_graph_ignores_nested_git_worktree above: that test
    # covers _iter_source_files (which files get scanned as modules at all);
    # this one covers _python_source_roots, a separate function that used a
    # raw, unfiltered rglob("__init__.py") to find valid absolute-import
    # roots. A worktree's own __init__.py chain got added as a second, bogus
    # root even though _iter_source_files never scanned the worktree's files
    # as modules - and since roots is an unordered set, resolving an
    # absolute import could nondeterministically pick the bogus worktree
    # root over the real one. A real file's own import then resolved to the
    # worktree's (never-scanned, nonexistent-as-a-module) duplicate path
    # instead of the real target, so the real target's imported_by stayed
    # empty and it was wrongly flagged as dead code - confirmed via a real
    # self-scan (github-app/app_server/main.py's own imports of its sibling
    # webhook handlers and API routers all resolved this way).
    repo = tmp_path / "repo"
    repo.mkdir()
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "real.py").write_text("def f():\n    pass\n")
    (repo / "main.py").write_text("from pkg.real import f\n")

    worktree = repo / "some-worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/some-worktree\n")
    worktree_pkg = worktree / "pkg"
    worktree_pkg.mkdir()
    (worktree_pkg / "__init__.py").write_text("")
    (worktree_pkg / "real.py").write_text("def f():\n    pass\n")

    modules, _, _ = build_module_graph(repo)
    real_module = next(m for m in modules if m["path"] == "pkg/real.py")
    assert real_module["imported_by"] == ["main.py"]


def test_build_module_graph_does_not_follow_a_symlinked_file_outside_the_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.py").write_text("def outside():\n    pass\n")
    (repo / "linked.py").symlink_to(tmp_path / "outside.py")
    (repo / "main.py").write_text("x = 1\n")

    modules, _, unparseable = build_module_graph(repo)

    assert unparseable == []
    assert {m["path"] for m in modules} == {"main.py"}


def test_build_module_graph_does_not_descend_into_a_symlinked_directory_outside_the_repo(tmp_path):
    # A symlinked directory isn't itself is_file(), so a naive per-path check
    # alone doesn't protect against it - Path.rglob("*") still recurses through
    # a symlinked directory's contents by default, parsing real files outside
    # the repo as if they were part of it.
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "module.py").write_text("def outside():\n    pass\n")
    (repo / "linked_dir").symlink_to(tmp_path / "outside")
    (repo / "main.py").write_text("x = 1\n")

    modules, _, unparseable = build_module_graph(repo)

    assert unparseable == []
    assert {m["path"] for m in modules} == {"main.py"}


def test_build_module_graph_extracts_typescript_imports(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "utils.ts").write_text(
        "export function add(a: number, b: number): number { return a + b; }\n"
    )
    (repo / "index.ts").write_text(
        "import { add } from './utils';\n\nfunction main(): number { return add(1, 2); }\n"
    )
    modules, dependency_graph, unparseable = build_module_graph(repo)
    by_path = {m["path"]: m for m in modules}
    assert "index.ts" in by_path
    assert "utils.ts" in by_path["index.ts"]["imports"]
    assert "add" in symbol_names(by_path["utils.ts"]["symbols"]["functions"])
    add_fn = next(f for f in by_path["utils.ts"]["symbols"]["functions"] if f["name"] == "add")
    assert add_fn["params"] == "(a: number, b: number)"
    assert unparseable == []


def test_build_module_graph_resolves_relative_imports(tmp_path):
    repo = tmp_path / "repo"
    app = repo / "app"
    routers = app / "routers"
    services = app / "services"
    routers.mkdir(parents=True)
    services.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (routers / "__init__.py").write_text("")
    (services / "__init__.py").write_text("")
    (app / "shared.py").write_text("def toplevel():\n    pass\n")
    (services / "sessions.py").write_text(
        "def collect_session_screenshots():\n    pass\n"
    )
    (routers / "helpers.py").write_text("def helper():\n    pass\n")
    (routers / "admin.py").write_text(
        "from ..services.sessions import collect_session_screenshots\n"
        "from . import helpers\n"
        "from .. import shared\n"
    )

    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    admin_imports = by_path["app/routers/admin.py"]["imports"]

    assert "app/services/sessions.py" in admin_imports
    assert "app/routers/helpers.py" in admin_imports
    assert "app/shared.py" in admin_imports


def test_build_module_graph_relative_sibling_import_does_not_become_parent_package(tmp_path):
    repo = tmp_path / "repo"
    app = repo / "app"
    routers = app / "routers"
    routers.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (routers / "__init__.py").write_text("")
    (routers / "helpers.py").write_text("def helper():\n    pass\n")
    (routers / "admin.py").write_text("from . import helpers\n")

    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    admin_imports = by_path["app/routers/admin.py"]["imports"]

    # a naive fix could turn "from . import helpers" (current package) into an
    # accidental "from .. import helpers" (parent package) if it inserts an extra
    # separator dot on top of the dot already present in "." - this must resolve
    # to the sibling module, not to app/__init__.py (the parent package)
    assert "app/routers/helpers.py" in admin_imports


def test_build_module_graph_resolves_absolute_imports_in_a_monorepo(tmp_path):
    # A monorepo can hold multiple independent Python projects, each with its own
    # top-level package one directory below the scanned root (repo/service_a/pkg_a/,
    # repo/service_b/pkg_b/) rather than directly inside it (repo/app/). Absolute
    # imports inside each project must resolve against that project's own root, not
    # the scanned repo root itself.
    repo = tmp_path / "repo"

    service_a = repo / "service_a"
    pkg_a = service_a / "pkg_a"
    pkg_a.mkdir(parents=True)
    (pkg_a / "__init__.py").write_text("")
    (pkg_a / "config.py").write_text("SETTING = 1\n")
    (pkg_a / "main.py").write_text("from pkg_a.config import SETTING\n")

    service_b = repo / "service_b"
    pkg_b = service_b / "pkg_b"
    pkg_b.mkdir(parents=True)
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "utils.py").write_text("def helper():\n    pass\n")
    (pkg_b / "app.py").write_text("import pkg_b.utils\n")

    modules, dependency_graph, unparseable = build_module_graph(repo)

    by_path = {m["path"]: m for m in modules}
    assert "service_a/pkg_a/config.py" in by_path["service_a/pkg_a/main.py"]["imports"]
    assert "service_b/pkg_b/utils.py" in by_path["service_b/pkg_b/app.py"]["imports"]


def test_build_module_graph_reuses_unchanged_modules_instead_of_reparsing(tmp_path, monkeypatch):
    # The whole point of unchanged_modules: a file whose content is known
    # not to have changed since it was last extracted should never be
    # handed to tree-sitter again - proven here by making parsing that
    # specific file raise, and confirming build_module_graph still
    # succeeds using the cached dict instead.
    repo = make_python_repo(tmp_path)

    from aletheore.scanner import graph as graph_module

    real_parser_class = graph_module.Parser

    class _FailingOnAuthParser:
        def __init__(self):
            self._inner = real_parser_class()

        @property
        def language(self):
            return self._inner.language

        @language.setter
        def language(self, value):
            self._inner.language = value

        def parse(self, source):
            # "AuthError" is unique to app/auth.py's own content - unlike
            # e.g. "login" (which routes.py's legitimate, still-parsed
            # content also references), this can't false-positive on a
            # different file that's correctly still being parsed.
            if b"AuthError" in source:
                raise AssertionError("app/auth.py should not be re-parsed - it's in unchanged_modules")
            return self._inner.parse(source)

    monkeypatch.setattr(graph_module, "Parser", _FailingOnAuthParser)

    cached_auth_module = {
        "path": "app/auth.py",
        "language": "python",
        "imports": ["app/config.py"],
        "imported_by": [],
        "symbols": {
            "functions": [{"name": "login", "start_line": 4, "end_line": 5}],
            "classes": [{"name": "AuthError", "start_line": 8, "end_line": 9}],
        },
    }

    modules, dependency_graph, unparseable = build_module_graph(
        repo, unchanged_modules={"app/auth.py": cached_auth_module}
    )

    by_path = {m["path"]: m for m in modules}
    assert by_path["app/auth.py"] is cached_auth_module
    assert unparseable == []


def test_build_module_graph_unchanged_modules_still_contribute_edges_and_imported_by(tmp_path):
    repo = make_python_repo(tmp_path)
    cached_auth_module = {
        "path": "app/auth.py",
        "language": "python",
        "imports": ["app/config.py"],
        "imported_by": [],
        "symbols": {
            "functions": [{"name": "login", "start_line": 4, "end_line": 5}],
            "classes": [{"name": "AuthError", "start_line": 8, "end_line": 9}],
        },
    }

    modules, dependency_graph, _ = build_module_graph(repo, unchanged_modules={"app/auth.py": cached_auth_module})

    edges = {tuple(edge) for edge in dependency_graph["edges"]}
    assert ("app/auth.py", "app/config.py") in edges
    by_path = {m["path"]: m for m in modules}
    # imported_by is always recomputed fresh from every module's imports
    # (cached or not), never trusted from the cached dict's stale value.
    assert "app/routes.py" in by_path["app/auth.py"]["imported_by"]
    assert "app/auth.py" in by_path["app/config.py"]["imported_by"]


def test_build_module_graph_ignores_unchanged_modules_entry_for_a_deleted_file(tmp_path):
    repo = make_python_repo(tmp_path)
    stale_cached_module = {
        "path": "app/removed.py",
        "language": "python",
        "imports": [],
        "imported_by": [],
        "symbols": {"functions": [], "classes": []},
    }

    modules, _, _ = build_module_graph(repo, unchanged_modules={"app/removed.py": stale_cached_module})

    assert "app/removed.py" not in {m["path"] for m in modules}


def test_build_module_graph_without_unchanged_modules_is_unchanged(tmp_path):
    repo = make_python_repo(tmp_path)
    with_none = build_module_graph(repo, unchanged_modules=None)
    without_param = build_module_graph(repo)

    assert with_none == without_param


def test_build_module_graph_records_module_level_constants(tmp_path):
    """A file can export a whole public API without a def or a class.
    Flask's signals.py is ten `x = _signals.signal(...)` assignments; on
    functions+classes alone it looked like an empty module, so it got no wiki
    page and produced no chunk the search index could retrieve."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "signals.py").write_text(
        "from blinker import Namespace\n\n"
        "_signals = Namespace()\n"
        "template_rendered = _signals.signal('template-rendered')\n"
        "request_started = _signals.signal('request-started')\n"
    )
    modules, _graph, _unparseable = build_module_graph(repo)
    constants = next(m for m in modules if m["path"] == "signals.py")["symbols"]["constants"]
    names = {c["name"] for c in constants}
    assert {"template_rendered", "request_started"} <= names
    assert next(c for c in constants if c["name"] == "template_rendered")["is_public"] is True
    assert next(c for c in constants if c["name"] == "_signals")["is_public"] is False


def test_build_module_graph_constants_are_module_level_only(tmp_path):
    """Locals and class attributes are not module exports; recording them
    would bury the real API in noise."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text(
        "TOP = 1\n"
        "TYPED: int = 2\n"
        "def f():\n    local_only = 3\n    return local_only\n"
        "class C:\n    class_attr = 4\n"
    )
    modules, _graph, _unparseable = build_module_graph(repo)
    names = {c["name"] for c in next(m for m in modules if m["path"] == "m.py")["symbols"]["constants"]}
    assert names == {"TOP", "TYPED"}


def test_build_module_graph_constants_skip_non_identifier_targets(tmp_path):
    """Tuple unpacking and attribute/subscript targets have no single name a
    reader could look up, so they are deliberately not recorded."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("import os\nKEEP = 1\na, b = 2, 3\nos.environ['X'] = '1'\n")
    modules, _graph, _unparseable = build_module_graph(repo)
    names = {c["name"] for c in next(m for m in modules if m["path"] == "m.py")["symbols"]["constants"]}
    assert names == {"KEEP"}


def test_build_module_graph_constants_key_present_for_non_python(tmp_path):
    """Only the Python extractor records bindings so far; every other language
    must still emit the key so consumers can read it unconditionally."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.js").write_text("export function f() { return 1; }\n")
    modules, _graph, _unparseable = build_module_graph(repo)
    assert next(m for m in modules if m["path"] == "a.js")["symbols"]["constants"] == []


def test_build_module_graph_javascript_commonjs_require_is_an_edge(tmp_path):
    """Handling only ESM `import` left every CommonJS codebase with an empty
    dependency graph: expressjs/express scanned as 141 modules with 0 resolved
    imports, so community detection emitted one cluster per file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.js").write_text("function helper() { return 1; }\nmodule.exports = { helper };\n")
    (repo / "main.js").write_text("const { helper } = require('./mod');\nfunction run() { return helper(); }\n")

    _modules, dependency_graph, _unparseable = build_module_graph(repo)
    assert ("main.js", "mod.js") in {tuple(e) for e in dependency_graph["edges"]}


def test_build_module_graph_javascript_assigned_function_expressions_are_symbols(tmp_path):
    """Express defines its whole surface as `app.use = function use(fn) {...}`.
    Counting only `function f(){}` left 103 of its 141 files with no symbols at
    all, so the search index had nothing but a fallback chunk to embed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.js").write_text(
        "const app = {};\n"
        "app.use = function use(fn) { return fn; };\n"
        "app.route = (path) => path;\n"
        "exports.init = function init() {};\n"
    )
    modules, _graph, _unparseable = build_module_graph(repo)
    names = symbol_names(modules[0]["symbols"]["functions"])
    assert {"use", "route", "init"} <= set(names)


def test_build_module_graph_typescript_extracts_interfaces_and_type_aliases(tmp_path):
    """colinhacks/zod has 972 `export type`/`export interface` declarations in
    its core src - extracting only function/class declarations left 39 files
    with zero other symbols and 210 of these invisible to the index."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "types.ts").write_text(
        "export interface Foo {\n  bar(): void;\n}\n\n"
        "export type Baz = { x: number };\n"
    )
    modules, _graph, _unparseable = build_module_graph(repo)
    names = symbol_names(modules[0]["symbols"]["classes"])
    assert {"Foo", "Baz"} <= set(names)


def test_build_module_graph_typescript_extracts_types_nested_in_a_namespace(tmp_path):
    """zod nests types inside `export namespace EnumUtil { ... }` -
    enumUtil.ts is entirely declarations like this and produced no symbols
    at all before this, since only top-level shapes were ever checked."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "enumUtil.ts").write_text(
        "export namespace EnumUtil {\n"
        "  export type Values<T> = T[keyof T];\n"
        "  export interface Inner {\n"
        "    y: string;\n"
        "  }\n"
        "}\n"
    )
    modules, _graph, _unparseable = build_module_graph(repo)
    names = symbol_names(modules[0]["symbols"]["classes"])
    assert {"Values", "Inner"} <= set(names)


def test_build_module_graph_constants_extracted_for_every_language(tmp_path):
    """A file can export a public API with no function or class - Flask's
    signals.py is ten assignments. That shape exists in every language, and
    only Python was recording it."""
    cases = {
        "a.js": ("javascript", "export const API_KEY = 'x';\n", "API_KEY"),
        "a.ts": ("typescript", "export const API_KEY: string = 'x';\n", "API_KEY"),
        "a.go": ("go", "package a\n\nconst MaxRetries = 3\n", "MaxRetries"),
        "a.rs": ("rust", "pub const MAX_RETRIES: i32 = 3;\n", "MAX_RETRIES"),
        "a.rb": ("ruby", "MAX_RETRIES = 3\n", "MAX_RETRIES"),
        "a.c": ("c", "#define MAX_RETRIES 3\n", "MAX_RETRIES"),
    }
    for filename, (_lang, body, expected) in cases.items():
        repo = tmp_path / filename.replace(".", "_")
        repo.mkdir()
        (repo / filename).write_text(body)
        modules, _graph, _unparseable = build_module_graph(repo)
        found = symbol_names(modules[0]["symbols"]["constants"])
        assert expected in found, f"{filename}: expected {expected}, got {found}"


def test_build_module_graph_has_modifier_does_not_false_positive_on_substring(tmp_path):
    """has_modifier used to check `w in head` (plain substring), so a
    declaration whose identifier merely contained a modifier word - e.g.
    "construct_id" containing "const" - was misclassified as a constant.
    An ordinary, non-const/non-static declaration with such a name must not
    be extracted."""
    cases = {
        "a.c": ("c", "int construct_id = 5;\n"),
        "a.java": ("java", "package a;\npublic class A { Object constants_registry = null; }\n"),
    }
    for filename, (_lang, body) in cases.items():
        repo = tmp_path / filename.replace(".", "_")
        repo.mkdir()
        (repo / filename).write_text(body)
        modules, _graph, _unparseable = build_module_graph(repo)
        found = symbol_names(modules[0]["symbols"]["constants"])
        assert found == [], f"{filename}: expected no constants, got {found}"


def test_build_module_graph_ruby_extracts_constants_declared_inside_class_or_module(tmp_path):
    """Ruby constants are idiomatically declared inside a module or class
    body, not at file scope - a real repo scan (sinatra/sinatra) found 10
    constants indented inside module/class bodies and 0 at true top level.
    Sinatra::Base::DROP_BODY_RESPONSES is exactly the API surface this
    feature exists to capture."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "base.rb").write_text(
        "module Sinatra\n"
        "  class Base\n"
        "    DROP_BODY = [204].freeze\n"
        "  end\n"
        "end\n"
    )
    modules, _graph, _unparseable = build_module_graph(repo)
    assert symbol_names(modules[0]["symbols"]["constants"]) == ["DROP_BODY"]


def test_build_module_graph_ruby_does_not_extract_a_constant_assigned_inside_a_method(tmp_path):
    """A capitalised assignment inside a def body is a method-local, not
    part of the type's public API - must stay excluded even though it sits
    inside a class body the same as a real constant does."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "base.rb").write_text(
        "module Sinatra\n"
        "  class Base\n"
        "    def foo\n"
        "      CONST = 1\n"
        "    end\n"
        "  end\n"
        "end\n"
    )
    modules, _graph, _unparseable = build_module_graph(repo)
    assert symbol_names(modules[0]["symbols"]["constants"]) == []


def test_build_module_graph_constants_key_always_present(tmp_path):
    """Consumers read symbols["constants"] unconditionally, so it must exist
    even for a language whose extractor records none."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.java").write_text("package a;\npublic class A { void f() {} }\n")
    modules, _graph, _unparseable = build_module_graph(repo)
    assert "constants" in modules[0]["symbols"]


def _java_symbols(repo: Path):
    """{name: entry} across functions and classes for the single Java module."""
    modules, _graph, _unparseable = build_module_graph(repo)
    module = next(m for m in modules if m["path"].endswith(".java"))
    symbols = module["symbols"]
    return {s["name"]: s for s in symbols["functions"] + symbols["classes"]}


def test_build_module_graph_java_visibility_reads_java_modifiers(tmp_path):
    """is_public was `not _is_nested_in_function(node)` - a fair proxy for
    Python, which has no access modifiers, but wrong for Java.
    docs_reference filters the generated API reference on this flag, so
    private methods were being published as public API."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Excluder.java").write_text(
        "package com.example;\n"
        "public final class Excluder {\n"
        "  public void shown() { }\n"
        "  private void hidden() { }\n"
        "  protected void alsoHidden() { }\n"
        "}\n"
    )

    symbols = _java_symbols(repo)

    assert symbols["shown"]["is_public"] is True
    assert symbols["hidden"]["is_public"] is False
    assert symbols["alsoHidden"]["is_public"] is False


def test_build_module_graph_java_interface_members_are_implicitly_public(tmp_path):
    """The absent-modifier case, and the reason this is not just "look for the
    public keyword": a member of an interface or annotation type carries no
    `modifiers` node at all and is public by Java's own rules. Treating that as
    private would hide google/gson's TypeAdapterFactory.create - a worse error
    than the one being fixed."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "TypeAdapterFactory.java").write_text(
        "package com.example;\n"
        "public interface TypeAdapterFactory {\n"
        "  TypeAdapter create(Gson gson, TypeToken type);\n"
        "}\n"
    )

    symbols = _java_symbols(repo)

    assert symbols["create"]["is_public"] is True


def test_build_module_graph_java_interface_symbol_is_pure_declaration_but_class_is_not(tmp_path):
    """AutoMapper's Mapper.cs shape: a file pairs a small interface with the
    real concrete implementation. The file-level is_declaration_only check no
    longer flags this file at all (see test_search_index.py), but the
    interface's own chunk should still carry the demotion on its own terms -
    is_pure_declaration is how build_chunks does that per-symbol instead of
    per-file."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Mapper.java").write_text(
        "package com.example;\n"
        "public interface IMapper {\n"
        "  Object map();\n"
        "}\n"
        "public class Mapper implements IMapper {\n"
        "  public Object map() { return doMap(); }\n"
        "  private Object doMap() { return null; }\n"
        "}\n"
    )

    symbols = _java_symbols(repo)

    assert symbols["IMapper"]["is_pure_declaration"] is True
    assert symbols["Mapper"]["is_pure_declaration"] is False


def test_build_module_graph_java_package_private_class_is_not_public(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Internal.java").write_text(
        "package com.example;\nclass Internal {\n  void helper() { }\n}\n"
    )

    symbols = _java_symbols(repo)

    assert symbols["Internal"]["is_public"] is False
