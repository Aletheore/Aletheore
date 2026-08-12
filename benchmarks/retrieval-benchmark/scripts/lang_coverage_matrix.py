"""Coverage matrix for every language the scanner claims to support.

Two files per language so a cross-file import edge is actually resolvable, each
defining a function, a class/struct, and a module-level constant. Checks what
the scanner recovers: imports, functions, classes, constants.
"""
import json, os, shutil, subprocess, sys

ROOT = "/private/tmp/langmatrix"

FIXTURES = {
    "python": {"dir": "py", "files": {
        "mod.py": "CONST_VALUE = 42\n\n\nclass Widget:\n    pass\n\n\ndef helper():\n    return CONST_VALUE\n",
        "main.py": "from mod import helper\n\nMAIN_CONST = 1\n\n\nclass App:\n    pass\n\n\ndef run():\n    return helper()\n"}},
    "javascript_esm": {"dir": "jsesm", "files": {
        "mod.js": "export const CONST_VALUE = 42;\nexport class Widget {}\nexport function helper() { return CONST_VALUE; }\n",
        "main.js": "import { helper } from './mod.js';\nexport const MAIN_CONST = 1;\nexport class App {}\nexport function run() { return helper(); }\n"}},
    "javascript_cjs": {"dir": "jscjs", "files": {
        "mod.js": "const CONST_VALUE = 42;\nclass Widget {}\nfunction helper() { return CONST_VALUE; }\nmodule.exports = { helper, Widget, CONST_VALUE };\n",
        "main.js": "const { helper } = require('./mod');\nconst MAIN_CONST = 1;\nclass App {}\nfunction run() { return helper(); }\nmodule.exports = { run, App, MAIN_CONST };\n"}},
    "typescript": {"dir": "ts", "files": {
        "mod.ts": "export const CONST_VALUE: number = 42;\nexport class Widget {}\nexport function helper(): number { return CONST_VALUE; }\n",
        "main.ts": "import { helper } from './mod';\nexport const MAIN_CONST: number = 1;\nexport class App {}\nexport function run(): number { return helper(); }\n"}},
    "go": {"dir": "go", "files": {
        "go.mod": "module example.com/demo\n\ngo 1.21\n",
        "mod/mod.go": "package mod\n\nconst ConstValue = 42\n\ntype Widget struct{}\n\nfunc Helper() int { return ConstValue }\n",
        "main.go": "package main\n\nimport \"example.com/demo/mod\"\n\nconst MainConst = 1\n\ntype App struct{}\n\nfunc Run() int { return mod.Helper() }\n"}},
    "rust": {"dir": "rs", "files": {
        "Cargo.toml": "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n",
        "src/lib.rs": "pub mod helper;\n\npub const MAIN_CONST: i32 = 1;\n\npub struct App;\n\npub fn run() -> i32 { helper::help() }\n",
        "src/helper.rs": "pub const CONST_VALUE: i32 = 42;\n\npub struct Widget;\n\npub fn help() -> i32 { CONST_VALUE }\n"}},
    "java": {"dir": "java", "files": {
        "src/com/example/Mod.java": "package com.example;\n\npublic class Mod {\n    public static final int CONST_VALUE = 42;\n    public static int helper() { return CONST_VALUE; }\n}\n",
        "src/com/example/Main.java": "package com.example;\n\nimport com.example.Mod;\n\npublic class Main {\n    public static final int MAIN_CONST = 1;\n    public static int run() { return Mod.helper(); }\n}\n"}},
    "ruby": {"dir": "rb", "files": {
        "mod.rb": "CONST_VALUE = 42\n\nclass Widget\nend\n\ndef helper\n  CONST_VALUE\nend\n",
        "main.rb": "require_relative 'mod'\n\nMAIN_CONST = 1\n\nclass App\nend\n\ndef run\n  helper\nend\n"}},
    # PSR-4 requires one class per file, named after the class - a fixture that
    # violates it makes correct resolution look broken.
    "php": {"dir": "php", "files": {
        "composer.json": '{"autoload":{"psr-4":{"App\\\\":"src/"}}}',
        "src/Widget.php": "<?php\nnamespace App;\n\nconst CONST_VALUE = 42;\n\nclass Widget {}\n\nfunction helper() { return CONST_VALUE; }\n",
        "src/Runner.php": "<?php\nnamespace App;\n\nuse App\\Widget;\n\nconst MAIN_CONST = 1;\n\nclass Runner {}\n\nfunction run() { return helper(); }\n"}},
    "c": {"dir": "c", "files": {
        "mod.h": "#ifndef MOD_H\n#define MOD_H\n#define CONST_VALUE 42\nint helper(void);\n#endif\n",
        "main.c": "#include \"mod.h\"\n\nstatic const int MAIN_CONST = 1;\n\nint run(void) { return helper(); }\n"}},
    "cpp": {"dir": "cpp", "files": {
        "mod.hpp": "#pragma once\nconst int CONST_VALUE = 42;\nclass Widget {};\nint helper();\n",
        "main.cpp": "#include \"mod.hpp\"\n\nconst int MAIN_CONST = 1;\n\nclass App {};\n\nint run() { return helper(); }\n"}},
    "csharp": {"dir": "cs", "files": {
        "Mod.cs": "namespace App.Lib;\n\npublic class Mod {\n    public const int ConstValue = 42;\n    public static int Helper() { return ConstValue; }\n}\n",
        "Main.cs": "using App.Lib;\n\nnamespace App.Run;\n\npublic class Main {\n    public const int MainConst = 1;\n    public static int Run() { return Mod.Helper(); }\n}\n"}},
}

shutil.rmtree(ROOT, ignore_errors=True)
rows = []
for lang, spec in FIXTURES.items():
    d = os.path.join(ROOT, spec["dir"])
    for rel, body in spec["files"].items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(body)
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.email=a@b.c", "-c", "user.name=t",
                    "commit", "-qm", "x"], cwd=d, check=True)
    subprocess.run(["aletheore", "scan", "."], cwd=d,
                   capture_output=True, text=True)
    air = os.path.join(d, ".aletheore", "air.json")
    if not os.path.exists(air):
        rows.append((lang, "SCAN FAILED", 0, 0, 0, 0)); continue
    e = json.load(open(air))
    mods = e["repository"]["modules"]
    imports = sum(len(m.get("imports") or []) for m in mods)
    fn = sum(len(m.get("symbols", {}).get("functions") or []) for m in mods)
    cl = sum(len(m.get("symbols", {}).get("classes") or []) for m in mods)
    co = sum(len(m.get("symbols", {}).get("constants") or []) for m in mods)
    rows.append((lang, f"{len(mods)} mods", imports, fn, cl, co))

print(f"{'language':18s} {'parsed':10s} {'imports':>8s} {'funcs':>6s} {'classes':>8s} {'consts':>7s}")
print("-" * 62)
for lang, parsed, i, f, c, k in rows:
    # C has no classes; absence there is correct, not a gap.
    expect_classes = lang != "c"
    flag = "" if (i and f and (c or not expect_classes)) else "   <-- GAP"
    print(f"{lang:18s} {parsed:10s} {i:8d} {f:6d} {c:8d} {k:7d}{flag}")
