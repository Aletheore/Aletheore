"""Incremental-cache staleness, Java wildcard imports, C# preprocessor-wrapped types and the C# edge cap."""
import copy
from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _edges(modules) -> dict[str, list[str]]:
    return {m["path"]: sorted(m["imports"]) for m in modules}


def _incremental(repo: Path, previous_modules, previous_sigs, changed: set[str], use_signatures=True):
    """Second scan that reuses every cached module whose own bytes did not change."""
    cache = {m["path"]: copy.deepcopy(m) for m in previous_modules if m["path"] not in changed}
    sigs_out: dict[str, str] = {}
    modules, _g, _u = build_module_graph(
        repo,
        unchanged_modules=cache,
        prior_signatures=previous_sigs if use_signatures else None,
        signatures_out=sigs_out,
    )
    return modules


def _first_scan(repo: Path):
    sigs: dict[str, str] = {}
    modules, _g, _u = build_module_graph(repo, signatures_out=sigs)
    return modules, sigs


def test_csharp_cached_module_goes_stale_when_another_file_changes_its_types(tmp_path):
    _write(tmp_path, "Widget.cs", "namespace N;\npublic class Widget {}\n")
    _write(tmp_path, "Use.cs", "namespace N;\npublic class Use { Widget w; }\n")
    first, sigs = _first_scan(tmp_path)
    assert _edges(first)["Use.cs"] == ["Widget.cs"]
    # Widget moves out of Widget.cs; Use.cs bytes are unchanged
    _write(tmp_path, "Widget.cs", "namespace N;\npublic class Other {}\n")
    _write(tmp_path, "Gadget.cs", "namespace N;\npublic class Widget {}\n")
    clean, _ = _first_scan(tmp_path)
    assert _edges(clean)["Use.cs"] == ["Gadget.cs"]
    # the old behaviour (no signatures) keeps the stale edge ...
    stale = _incremental(tmp_path, first, None, {"Widget.cs", "Gadget.cs"}, use_signatures=False)
    assert _edges(stale)["Use.cs"] == ["Widget.cs"]
    # ... and with signatures the incremental scan equals the clean one
    fixed = _incremental(tmp_path, first, sigs, {"Widget.cs", "Gadget.cs"})
    assert _edges(fixed) == _edges(clean)


def test_java_same_package_cached_module_goes_stale_when_a_type_is_added(tmp_path):
    _write(tmp_path, "src/a/Use.java", "package a;\npublic class Use { Helper h; }\n")
    first, sigs = _first_scan(tmp_path)
    assert _edges(first)["src/a/Use.java"] == []
    _write(tmp_path, "src/a/Helper.java", "package a;\npublic class Helper {}\n")
    clean, _ = _first_scan(tmp_path)
    assert _edges(clean)["src/a/Use.java"] == ["src/a/Helper.java"]
    stale = _incremental(tmp_path, first, None, {"src/a/Helper.java"}, use_signatures=False)
    assert _edges(stale)["src/a/Use.java"] == []
    fixed = _incremental(tmp_path, first, sigs, {"src/a/Helper.java"})
    assert _edges(fixed) == _edges(clean)


def test_python_cached_module_goes_stale_when_an_imported_file_appears(tmp_path):
    _write(tmp_path, "main.py", "import helper\n")
    first, sigs = _first_scan(tmp_path)
    assert _edges(first)["main.py"] == []
    _write(tmp_path, "helper.py", "x = 1\n")
    clean, _ = _first_scan(tmp_path)
    assert _edges(clean)["main.py"] == ["helper.py"]
    fixed = _incremental(tmp_path, first, sigs, {"helper.py"})
    assert _edges(fixed) == _edges(clean)


def test_unrelated_edit_keeps_cached_modules(tmp_path):
    _write(tmp_path, "Widget.cs", "namespace N;\npublic class Widget { int a; }\n")
    _write(tmp_path, "Use.cs", "namespace N;\npublic class Use { Widget w; }\n")
    first, sigs = _first_scan(tmp_path)
    # a body-only edit: same types, same namespaces -> same signature -> Use.cs is not re-extracted
    _write(tmp_path, "Widget.cs", "namespace N;\npublic class Widget { int a; int b; }\n")
    cache = {m["path"]: m for m in first if m["path"] != "Widget.cs"}
    modules, _g, _u = build_module_graph(tmp_path, unchanged_modules=cache, prior_signatures=sigs)
    assert any(m is cache["Use.cs"] for m in modules)
    assert _edges(modules)["Use.cs"] == ["Widget.cs"]


def test_cache_without_saved_signatures_is_not_trusted_once(tmp_path):
    _write(tmp_path, "Widget.cs", "namespace N;\npublic class Widget {}\n")
    _write(tmp_path, "Use.cs", "namespace N;\npublic class Use { Widget w; }\n")
    first, _sigs = _first_scan(tmp_path)
    cache = {m["path"]: m for m in first}
    modules, _g, _u = build_module_graph(tmp_path, unchanged_modules=cache, prior_signatures={})
    assert not any(m is cache["Use.cs"] for m in modules)  # re-extracted, same edges
    assert _edges(modules) == _edges(first)


# ---- Java wildcard imports ----

def test_java_wildcard_import_links_only_files_whose_types_are_used(tmp_path):
    for name in ("One", "Two", "Three"):
        _write(tmp_path, f"src/lib/{name}.java", f"package lib;\npublic class {name} {{}}\n")
    _write(tmp_path, "src/app/Use.java", "package app;\nimport lib.*;\npublic class Use { One o; }\n")
    assert _edges(build_module_graph(tmp_path)[0])["src/app/Use.java"] == ["src/lib/One.java"]


def test_java_nested_wildcard_import_links_the_outer_class_file(tmp_path):
    _write(tmp_path, "src/lib/Outer.java", "package lib;\npublic class Outer { public static class In {} }\n")
    _write(tmp_path, "src/app/Use.java", "package app;\nimport lib.Outer.*;\npublic class Use { In i; }\n")
    assert _edges(build_module_graph(tmp_path)[0])["src/app/Use.java"] == ["src/lib/Outer.java"]


# ---- C# ----

def test_csharp_types_inside_preprocessor_blocks_are_indexed(tmp_path):
    _write(tmp_path, "Poly.cs", "#if FLAG\nnamespace N;\npublic class PolyThing {}\n#endif\n")
    _write(tmp_path, "Use.cs", "namespace N;\npublic class Use { PolyThing p; }\n")
    assert _edges(build_module_graph(tmp_path)[0])["Use.cs"] == ["Poly.cs"]


def test_csharp_file_naming_many_types_keeps_all_edges(tmp_path):
    names = [f"Entity{i}Thing" for i in range(60)]
    for n in names:
        _write(tmp_path, f"{n}.cs", f"namespace N;\npublic class {n} {{}}\n")
    body = " ".join(f"{n} f{i};" for i, n in enumerate(names))
    _write(tmp_path, "Many.cs", f"namespace N;\npublic class Many {{ {body} }}\n")
    assert len(_edges(build_module_graph(tmp_path)[0])["Many.cs"]) == 60
