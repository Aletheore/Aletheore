import json
from pathlib import Path

from aletheore.scanner.graph import build_module_graph
from aletheore.scanner.js_resolve import _is_absolute, _load_tsconfig, _strip_jsonc


def write(repo: Path, rel: str, text: str = "export {};\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def imports_of(repo: Path, path: str) -> list[str]:
    modules, _, _ = build_module_graph(repo)
    return sorted(next(m for m in modules if m["path"] == path)["imports"])


def test_dotted_name_is_not_treated_as_an_extension(tmp_path):
    write(tmp_path, "foo.ts")
    write(tmp_path, "foo.test.ts")
    write(tmp_path, "main.ts", "import './foo.test';\n")
    assert imports_of(tmp_path, "main.ts") == ["foo.test.ts"]


def test_js_specifier_maps_to_ts_family_sources(tmp_path):
    for f in ("a.ts", "b.tsx", "c.mts", "d.cts"):
        write(tmp_path, f)
    write(tmp_path, "main.ts", "import './a.js';\nimport './b.js';\nimport './c.mjs';\nimport './d.cjs';\n")
    assert imports_of(tmp_path, "main.ts") == ["a.ts", "b.tsx", "c.mts", "d.cts"]


def test_directory_index_forms(tmp_path):
    for d in ("dir", "dir2", "dir3"):
        write(tmp_path, f"{d}/index.ts")
    write(tmp_path, "main.ts", "import './dir/index.js';\nimport './dir2';\nimport './dir3/';\n")
    assert imports_of(tmp_path, "main.ts") == ["dir/index.ts", "dir2/index.ts", "dir3/index.ts"]


def test_file_beats_directory_of_the_same_name(tmp_path):
    write(tmp_path, "both.ts")
    write(tmp_path, "both/index.ts")
    write(tmp_path, "main.ts", "import './both';\n")
    assert imports_of(tmp_path, "main.ts") == ["both.ts"]


def test_asset_imports_are_not_graph_edges(tmp_path):
    write(tmp_path, "data.json", "{}")
    write(tmp_path, "style.css", "x{}")
    write(tmp_path, "logo.png", "x")
    write(tmp_path, "main.ts", "import d from './data.json';\nimport './style.css';\nimport l from './logo.png';\nconst r = require('./data.json');\n")
    assert imports_of(tmp_path, "main.ts") == []


def test_every_static_form_creates_an_edge(tmp_path):
    for f in ("t", "u", "s", "r", "ns", "dyn", "tpl", "it", "side"):
        write(tmp_path, f"{f}.ts")
    write(tmp_path, "req.cjs", "module.exports = 1;\n")
    write(
        tmp_path,
        "main.ts",
        "import type { T } from './t.js';\nimport { type U } from './u.js';\n"
        "export * from './s.js';\nexport { x } from './r.js';\nexport * as ns from './ns.js';\n"
        "const l = () => import('./dyn.js');\nconst p = () => import(`./tpl.js`);\n"
        "try { require('./req.cjs'); } catch (e) {}\nimport './side.js';\n",
    )
    assert imports_of(tmp_path, "main.ts") == sorted(
        ["t.ts", "u.ts", "s.ts", "r.ts", "ns.ts", "dyn.ts", "tpl.ts", "req.cjs", "side.ts"]
    )


def test_interpolated_template_import_is_not_an_edge(tmp_path):
    write(tmp_path, "x.ts")
    write(tmp_path, "main.ts", "const n = 'x';\nconst p = () => import(`./${n}.js`);\n")
    assert imports_of(tmp_path, "main.ts") == []


def test_new_url_asset_reference_is_not_an_edge(tmp_path):
    write(tmp_path, "worker.ts")
    write(tmp_path, "main.ts", "const u = new URL('./worker.ts', import.meta.url);\n")
    assert imports_of(tmp_path, "main.ts") == []


# ---- review fixes for js_resolve ----
def test_windows_absolute_base_url_is_not_joined(tmp_path):
    assert _is_absolute("C:/repo/src") and _is_absolute("C:\\repo\\src") and _is_absolute("/abs")
    assert not _is_absolute("./src") and not _is_absolute("src")
    cfg = tmp_path / "tsconfig.json"
    cfg.write_text(json.dumps({"compilerOptions": {"baseUrl": "C:/repo/src"}}))
    assert _load_tsconfig(cfg)["baseUrl"] == "C:/repo/src"
    rel = tmp_path / "sub" / "tsconfig.json"
    rel.parent.mkdir()
    rel.write_text(json.dumps({"compilerOptions": {"baseUrl": "."}}))
    assert _load_tsconfig(rel)["baseUrl"] == str(rel.parent.resolve())


def test_trailing_comma_removal_leaves_strings_alone():
    text = '{"banner": "build done,}", "list": [1, 2,], "o": {"a": "x,]",\n},}'
    assert json.loads(_strip_jsonc(text)) == {"banner": "build done,}", "list": [1, 2], "o": {"a": "x,]"}}


def test_malformed_tsconfig_values_are_skipped_not_raised(tmp_path):
    write(tmp_path, "tsconfig.json", json.dumps({"compilerOptions": {"paths": "oops", "baseUrl": 5, "customConditions": "x"}, "extends": 3}))
    write(tmp_path, "pkg/tsconfig.json", json.dumps({"compilerOptions": {"paths": ["a"]}}))
    write(tmp_path, "pkg/package.json", json.dumps({"name": ["nope"], "exports": 7}))
    write(tmp_path, "pkg/src/ok.ts")
    write(tmp_path, "pkg/main.ts", "import './src/ok';\nimport x from '@/y';\nimport z from 'lodash';\n")
    assert imports_of(tmp_path, "pkg/main.ts") == ["pkg/src/ok.ts"]


def test_malformed_paths_entries_are_dropped_but_good_ones_kept(tmp_path):
    write(tmp_path, "tsconfig.json", json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": "src/*", "~/*": [1, "src/*"]}}}))
    write(tmp_path, "src/x.ts")
    write(tmp_path, "main.ts", "import '~/x';\nimport '@/x';\n")
    assert imports_of(tmp_path, "main.ts") == ["src/x.ts"]


def test_extends_list_is_accepted(tmp_path):
    write(tmp_path, "base.json", json.dumps({"compilerOptions": {"paths": {"~/*": ["./src/*"]}}}))
    write(tmp_path, "tsconfig.json", json.dumps({"extends": ["./base.json"]}))
    write(tmp_path, "src/x.ts")
    write(tmp_path, "src/y.ts", "import '~/x';\n")
    assert imports_of(tmp_path, "src/y.ts") == ["src/x.ts"]
