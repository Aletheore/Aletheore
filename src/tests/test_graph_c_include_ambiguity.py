"""C include paths without known compiler flags are not exact resolutions."""

from aletheore.scanner.graph import build_module_graph


def _scan(tmp_path, files):
    for name, contents in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    modules, _, _ = build_module_graph(tmp_path)
    return {m["path"]: m for m in modules}


def test_ambiguous_c_header_is_not_chosen_by_directory_proximity(tmp_path):
    modules = _scan(tmp_path, {
        "contrib/tool/main.c": '#include "mem.h"\n',
        "contrib/kernel/mem.h": "int kernel;\n",
        "lib/common/mem.h": "int common;\n",
    })
    assert modules["contrib/tool/main.c"]["imports"] == []


def test_unique_suffix_match_is_inferred(tmp_path):
    modules = _scan(tmp_path, {
        "tools/main.c": '#include "detail/helper.h"\n',
        "library/detail/helper.h": "int helper;\n",
    })
    importer = modules["tools/main.c"]
    assert importer["imports"] == ["library/detail/helper.h"]
    assert importer["import_confidence"]["library/detail/helper.h"] == "inferred"


def test_local_quoted_include_stays_exact(tmp_path):
    modules = _scan(tmp_path, {
        "src/main.c": '#include "helper.h"\n',
        "src/helper.h": "int helper;\n",
    })
    importer = modules["src/main.c"]
    assert importer["imports"] == ["src/helper.h"]
    assert "src/helper.h" not in importer.get("import_confidence", {})


def test_include_search_root_is_inferred(tmp_path):
    modules = _scan(tmp_path, {
        "src/main.c": '#include <project.h>\n',
        "include/project.h": "int project;\n",
    })
    importer = modules["src/main.c"]
    assert importer["imports"] == ["include/project.h"]
    assert importer["import_confidence"]["include/project.h"] == "inferred"


def test_header_includers_get_the_same_treatment(tmp_path):
    modules = _scan(tmp_path, {
        "lib/a/util.h": '#include "mem.h"\n',
        "contrib/kernel/mem.h": "int kernel;\n",
        "lib/common/mem.h": "int common;\n",
        "lib/a/other.h": '#include <project.h>\n',
        "include/project.h": "int project;\n",
    })
    assert modules["lib/a/util.h"]["imports"] == []
    other = modules["lib/a/other.h"]
    assert other["imports"] == ["include/project.h"]
    assert other["import_confidence"]["include/project.h"] == "inferred"
