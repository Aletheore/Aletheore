import json
from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def write(repo: Path, rel: str, text: str = "") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def imports_of(repo: Path) -> dict[str, list[str]]:
    modules, _, _ = build_module_graph(repo)
    return {m["path"]: sorted(m["imports"]) for m in modules}


def test_workspace_package_exports_with_custom_condition(tmp_path):
    repo = tmp_path / "r"
    write(repo, "pnpm-workspace.yaml", "packages:\n  - packages/*\n")
    write(repo, "tsconfig.json", '{ // comment\n "compilerOptions": {"customConditions": ["@x/source"],},\n}')
    write(
        repo,
        "packages/lib/package.json",
        json.dumps(
            {
                "name": "lib",
                "exports": {
                    ".": {"@x/source": "./src/index.ts", "import": "./dist/index.js"},
                    "./sub": {"@x/source": "./src/sub/index.ts", "import": "./dist/sub.js"},
                },
            }
        ),
    )
    write(repo, "packages/lib/src/index.ts", "export const a = 1;\n")
    write(repo, "packages/lib/src/sub/index.ts", "export const b = 1;\n")
    write(repo, "packages/app/main.ts", "import { a } from 'lib';\nimport { b } from 'lib/sub';\nimport z from 'external';\n")
    got = imports_of(repo)
    assert got["packages/app/main.ts"] == ["packages/lib/src/index.ts", "packages/lib/src/sub/index.ts"]


def test_self_reference_and_main_fallback_without_exports(tmp_path):
    repo = tmp_path / "r"
    write(repo, "package.json", json.dumps({"name": "pkg", "main": "./lib/entry.js"}))
    write(repo, "lib/entry.js", "module.exports = {};\n")
    write(repo, "lib/util.js", "module.exports = {};\n")
    write(repo, "test/a.js", "const p = require('pkg');\nconst u = require('pkg/lib/util.js');\n")
    got = imports_of(repo)
    assert got["test/a.js"] == ["lib/entry.js", "lib/util.js"]


def test_exports_condition_order_prefers_existing_import_target(tmp_path):
    repo = tmp_path / "r"
    write(
        repo,
        "package.json",
        json.dumps({"name": "ax", "exports": {".": {"types": "./index.d.ts", "default": {"require": "./dist/ax.cjs", "default": "./index.js"}}}}),
    )
    write(repo, "index.js", "export default 1;\n")
    write(repo, "t.js", "import ax from 'ax';\n")
    assert imports_of(repo)["t.js"] == ["index.js"]


def test_exports_wildcard_subpath(tmp_path):
    repo = tmp_path / "r"
    write(repo, "package.json", json.dumps({"name": "w", "exports": {"./locales/*": "./src/locales/*.ts"}}))
    write(repo, "src/locales/en.ts", "export default {};\n")
    write(repo, "a.ts", "import en from 'w/locales/en';\n")
    assert imports_of(repo)["a.ts"] == ["src/locales/en.ts"]


def test_tsconfig_paths_and_base_url(tmp_path):
    repo = tmp_path / "r"
    write(repo, "tsconfig.json", json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["src/app/*"]}}}))
    write(repo, "src/app/util.ts", "export const u = 1;\n")
    write(repo, "src/other.ts", "export const o = 1;\n")
    write(repo, "src/main.ts", "import { u } from '@app/util';\nimport { o } from 'src/other';\n")
    assert imports_of(repo)["src/main.ts"] == ["src/app/util.ts", "src/other.ts"]


def test_tsconfig_extends_paths_relative_to_parent(tmp_path):
    repo = tmp_path / "r"
    write(repo, "tsconfig.base.json", json.dumps({"compilerOptions": {"paths": {"~/*": ["./src/*"]}}}))
    write(repo, "tsconfig.json", json.dumps({"extends": "./tsconfig.base.json"}))
    write(repo, "src/x.ts", "export const x = 1;\n")
    write(repo, "src/y.ts", "import { x } from '~/x';\n")
    assert imports_of(repo)["src/y.ts"] == ["src/x.ts"]


def test_external_package_never_resolves_into_node_modules(tmp_path):
    repo = tmp_path / "r"
    write(repo, "package.json", json.dumps({"name": "me"}))
    write(repo, "node_modules/react/index.js", "x\n")
    write(repo, "a.js", "import r from 'react';\nimport f from 'fs';\nimport n from 'node:path';\n")
    assert imports_of(repo)["a.js"] == []


def test_module_extensions_are_scanned_and_resolved(tmp_path):
    repo = tmp_path / "r"
    write(repo, "a.cjs", "const b = require('./b.cjs');\n")
    write(repo, "b.cjs", "module.exports = 1;\n")
    write(repo, "c.mjs", "import './d.mjs';\nexport const c = 1;\n")
    write(repo, "d.mjs", "export const d = 1;\n")
    write(repo, "e.mts", "import { f } from './f.mjs';\n")
    write(repo, "f.mts", "export const f = 1;\n")
    write(repo, "g.cts", "import h = require('./h.cjs');\n")
    write(repo, "h.cts", "export const h = 1;\n")
    modules, _, _ = build_module_graph(repo)
    langs = {m["path"]: m["language"] for m in modules}
    assert langs == {
        "a.cjs": "javascript", "b.cjs": "javascript", "c.mjs": "javascript", "d.mjs": "javascript",
        "e.mts": "typescript", "f.mts": "typescript", "g.cts": "typescript", "h.cts": "typescript",
    }
    got = {m["path"]: m["imports"] for m in modules}
    assert got["a.cjs"] == ["b.cjs"]
    assert got["c.mjs"] == ["d.mjs"]
    assert got["e.mts"] == ["f.mts"]
