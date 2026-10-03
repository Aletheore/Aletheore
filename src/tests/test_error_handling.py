from pathlib import Path

from aletheore.error_handling import MAX_HANDLERS, MAX_RAISE_SITES, map_error_handling


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _names(items: list[dict], key: str = "name") -> set[str]:
    return {item[key] for item in items}


def test_python_error_types_follow_the_hierarchy_through_repo_classes(tmp_path):
    _write(tmp_path, "pkg/errors.py", (
        "class AppError(Exception):\n    pass\n\n"
        "class ConfigError(AppError):\n    pass\n\n"
        "class NotAnError:\n    pass\n"
    ))
    result = map_error_handling(tmp_path)
    assert result["checked"] is True
    by_name = {t["name"]: t for t in result["error_types"]}
    assert set(by_name) == {"AppError", "ConfigError"}
    assert by_name["ConfigError"]["bases"] == ["AppError"]
    assert by_name["AppError"]["file"] == "pkg/errors.py"
    assert by_name["AppError"]["line"] == 1


def test_python_raise_sites_record_type_line_and_enclosing_function(tmp_path):
    _write(tmp_path, "a.py", (
        "def load(path):\n"
        "    if not path:\n"
        "        raise ValueError('empty')\n"
        "    try:\n"
        "        return open(path)\n"
        "    except OSError:\n"
        "        raise\n"
        "def other():\n"
        "    raise CustomError\n"
    ))
    sites = {(s["line"], s["error_type"], s["function"]) for s in map_error_handling(tmp_path)["raise_sites"]}
    assert (3, "ValueError", "load") in sites
    assert (7, "(re-raise)", "load") in sites
    assert (9, "CustomError", "other") in sites


def test_python_handlers_record_what_they_catch(tmp_path):
    _write(tmp_path, "a.py", (
        "def f():\n"
        "    try:\n        pass\n"
        "    except (KeyError, ValueError):\n        pass\n"
        "    except OSError as exc:\n        pass\n"
        "    except:\n        pass\n"
    ))
    handlers = {h["line"]: h["catches"] for h in map_error_handling(tmp_path)["handlers"]}
    assert handlers[4] == ["KeyError", "ValueError"]
    assert handlers[6] == ["OSError"]
    assert handlers[8] == ["(any)"]


def test_cpp_error_types_throw_sites_and_catch_handlers(tmp_path):
    _write(tmp_path, "include/err.h", (
        "class format_error : public std::runtime_error {};\n"
        "struct plain {};\n"
    ))
    _write(tmp_path, "src/use.cc", (
        "void g() {\n"
        "  throw std::out_of_range(\"x\");\n"
        "  FMT_THROW(format_error(\"bad\"));\n"
        "  try {} catch (const format_error& e) {} catch (...) {}\n"
        "}\n"
    ))
    result = map_error_handling(tmp_path)
    assert _names(result["error_types"]) == {"format_error"}
    sites = {(s["file"], s["line"], s["error_type"], s["function"]) for s in result["raise_sites"]}
    assert ("src/use.cc", 2, "std::out_of_range", "g") in sites
    assert ("src/use.cc", 3, "format_error", "g") in sites
    all_catches = [c for h in result["handlers"] for c in h["catches"]]
    assert "format_error" in all_catches and "..." in all_catches


def test_by_error_type_counts_raised_and_caught_and_is_sorted_by_use(tmp_path):
    _write(tmp_path, "a.py", (
        "class AppError(Exception):\n    pass\n"
        "def f():\n"
        "    raise AppError()\n"
        "def g():\n"
        "    raise AppError()\n"
        "def h():\n"
        "    try:\n        pass\n    except AppError:\n        pass\n"
        "    raise KeyError()\n"
    ))
    summary = map_error_handling(tmp_path)["by_error_type"]
    top = summary[0]
    assert (top["name"], top["raised"], top["caught"]) == ("AppError", 2, 1)
    assert top["defined_in"] == "a.py"
    assert {s["name"] for s in summary} == {"AppError", "KeyError"}


def test_ignored_directories_and_other_languages_are_skipped(tmp_path):
    _write(tmp_path, "node_modules/x/e.py", "def f():\n    raise ValueError()\n")
    _write(tmp_path, "notes.md", "raise ValueError")
    assert map_error_handling(tmp_path)["raise_sites"] == []


def test_output_is_capped_and_says_so(tmp_path):
    body = "".join(f"def f{i}():\n    raise ValueError()\n" for i in range(MAX_RAISE_SITES + 5))
    _write(tmp_path, "many.py", body)
    result = map_error_handling(tmp_path)
    assert len(result["raise_sites"]) == MAX_RAISE_SITES
    assert result["truncated"] is True
    # the summary counts everything, not just what fit
    assert result["by_error_type"][0]["raised"] == MAX_RAISE_SITES + 5


def test_an_empty_repo_is_checked_and_empty(tmp_path):
    result = map_error_handling(tmp_path)
    assert result == {
        "checked": True, "error_types": [], "raise_sites": [], "handlers": [],
        "by_error_type": [], "truncated": False,
    }


def test_handler_cap_exists():
    assert MAX_HANDLERS > 0


def test_test_framework_throw_macros_are_not_throw_sites(tmp_path):
    _write(tmp_path, "t.cc", "void t() { EXPECT_THROW(f(), std::runtime_error); FMT_THROW(my_error(\"x\")); }\n")
    types = [s["error_type"] for s in map_error_handling(tmp_path)["raise_sites"]]
    assert types == ["my_error"]


def test_a_cpp_error_class_behind_an_attribute_macro_is_still_found(tmp_path):
    _write(tmp_path, "base.h", (
        "// class commented_out : public std::runtime_error {\n"
        "class FMT_SO_VISIBILITY(\"default\") format_error : public std::runtime_error {\n"
        " public:\n  using std::runtime_error::runtime_error;\n};\n"
    ))
    types = {t["name"]: t["line"] for t in map_error_handling(tmp_path)["error_types"]}
    assert types == {"format_error": 2}


def _scan(tmp_path, rel, text):
    _write(tmp_path, rel, text)
    return map_error_handling(tmp_path)


def _sites(result):
    return {(s["line"], s["error_type"], s["function"]) for s in result["raise_sites"]}


def _catches(result):
    return {h["line"]: h["catches"] for h in result["handlers"]}


def test_javascript_errors(tmp_path):
    r = _scan(tmp_path, "a.js", (
        "class AppError extends Error {}\n"
        "function load() {\n"
        "  try { run(); } catch (e) { throw e; }\n"
        "  throw new AppError('x');\n"
        "}\n"
    ))
    assert _names(r["error_types"]) == {"AppError"}
    assert (3, "(re-raise)", "load") in _sites(r)
    assert (4, "AppError", "load") in _sites(r)
    assert _catches(r)[3] == ["(any)"]


def test_typescript_errors_and_typed_catch(tmp_path):
    r = _scan(tmp_path, "a.ts", (
        "class E2 extends AppError {}\nclass AppError extends Error {}\n"
        "function f(): void {\n  try {} catch (e: MyError) {}\n  throw makeErr();\n}\n"
    ))
    assert _names(r["error_types"]) == {"E2", "AppError"}
    assert _catches(r)[4] == ["MyError"]
    assert (5, "makeErr", "f") in _sites(r)


def test_java_errors(tmp_path):
    r = _scan(tmp_path, "A.java", (
        "class AppEx extends RuntimeException {}\n"
        "class A {\n  void f() {\n    try {} catch (IOException | AppEx e) {}\n    throw new AppEx(\"x\");\n  }\n}\n"
    ))
    assert _names(r["error_types"]) == {"AppEx"}
    assert _catches(r)[4] == ["IOException", "AppEx"]
    assert (5, "AppEx", "f") in _sites(r)


def test_csharp_errors(tmp_path):
    r = _scan(tmp_path, "A.cs", (
        "class AppEx : Exception {}\n"
        "class A {\n  void F() {\n    try {} catch (IOException e) {} catch {}\n    throw new AppEx(\"x\");\n  }\n}\n"
    ))
    assert _names(r["error_types"]) == {"AppEx"}
    assert _catches(r)[4] == ["IOException"] or "IOException" in [c for h in r["handlers"] for c in h["catches"]]
    assert (5, "AppEx", "F") in _sites(r)
    assert "(any)" in [c for h in r["handlers"] for c in h["catches"]]


def test_php_errors(tmp_path):
    r = _scan(tmp_path, "a.php", (
        "<?php\nclass AppEx extends \\RuntimeException {}\n"
        "function f() {\n  try {} catch (IOException | AppEx $e) {}\n  throw new AppEx('x');\n}\n"
    ))
    assert _names(r["error_types"]) == {"AppEx"}
    assert _catches(r)[4] == ["IOException", "AppEx"]
    assert (5, "AppEx", "f") in _sites(r)


def test_kotlin_errors(tmp_path):
    r = _scan(tmp_path, "a.kt", (
        "class AppEx : RuntimeException()\n"
        "fun f() {\n  try {} catch (e: IOException) {}\n  throw AppEx(\"x\")\n}\n"
    ))
    assert _names(r["error_types"]) == {"AppEx"}
    assert "IOException" in [c for h in r["handlers"] for c in h["catches"]]
    assert any(t == "AppEx" for _, t, _ in _sites(r))


def test_ruby_errors(tmp_path):
    r = _scan(tmp_path, "a.rb", (
        "class AppError < StandardError; end\n"
        "def f\n  raise AppError, 'x'\nrescue IOError, AppError => e\n  raise AppError.new('y')\nrescue\n  raise\nend\n"
    ))
    assert _names(r["error_types"]) == {"AppError"}
    assert {t for _, t, _ in _sites(r)} >= {"AppError", "(re-raise)"}
    catches = [c for h in r["handlers"] for c in h["catches"]]
    assert "IOError" in catches and "AppError" in catches and "(any)" in catches


def test_swift_errors(tmp_path):
    r = _scan(tmp_path, "a.swift", (
        "enum AppErr: Error { case bad }\n"
        "func f() throws {\n  do { try g() } catch AppErr.bad { } catch { }\n  throw AppErr.bad\n}\n"
    ))
    assert _names(r["error_types"]) == {"AppErr"}
    assert any(t == "AppErr" for _, t, _ in _sites(r))
    catches = [c for h in r["handlers"] for c in h["catches"]]
    assert "AppErr" in catches and "(any)" in catches


def test_go_errors_are_values_sentinels_custom_types_and_panics(tmp_path):
    r = _scan(tmp_path, "a.go", (
        "package p\n"
        "var ErrNotFound = errors.New(\"nf\")\n"
        "type MyErr struct{}\n"
        "func (e *MyErr) Error() string { return \"x\" }\n"
        "func f() error {\n"
        "  defer func() { recover() }()\n"
        "  if errors.Is(err, ErrNotFound) { panic(\"boom\") }\n"
        "  return fmt.Errorf(\"w: %w\", err)\n"
        "}\n"
    ))
    assert _names(r["error_types"]) == {"ErrNotFound", "MyErr"}
    types = {t for _, t, _ in _sites(r)}
    assert "fmt.Errorf" in types and "(value)" in types  # the panic string
    assert "errors.New" not in types  # the sentinel's own constructor is a definition, not a raise
    catches = [c for h in r["handlers"] for c in h["catches"]]
    assert "(any)" in catches and "ErrNotFound" in catches


def test_rust_errors(tmp_path):
    r = _scan(tmp_path, "a.rs", (
        "#[derive(Debug, thiserror::Error)]\nenum AppError { #[error(\"x\")] Bad }\n"
        "struct E2;\nimpl std::error::Error for E2 {}\nstruct NotAnError;\n"
        "fn f() -> Result<(), AppError> {\n  if x { return Err(AppError::Bad); }\n  panic!(\"no\");\n}\n"
    ))
    assert _names(r["error_types"]) == {"AppError", "E2"}
    types = {t for _, t, _ in _sites(r)}
    assert "AppError" in types and "panic!" in types
