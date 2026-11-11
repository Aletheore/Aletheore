"""Regressions for the gaps found by scoring the real-fix pairs (MISS-REPORT.md): request binding in Go, promisified
exec in JS, Rack env in Ruby."""
from pathlib import Path

from aletheore.flow.discover import discover_repo


def _write(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return tmp_path


def _rules(tmp_path: Path, suffix: str) -> list[tuple[str, int]]:
    return sorted((f["rule_id"].split(".")[-1], f["line"]) for f in discover_repo(tmp_path) if f["rule_id"].endswith(suffix))


# ---- Go: binding a request body into a value

GO_STRUCT = ('package main\nimport ("net/http"; "os"; "path"; "encoding/json")\ntype Req struct{ P string }\n'
             'func h(w http.ResponseWriter, r *http.Request) {\n\tvar d Req\n\tjson.NewDecoder(r.Body).Decode(&d)\n'
             '\tb, _ := os.ReadFile(path.Join("/srv", d.P))\n\tw.Write(b)\n}\n')


def test_go_json_decoder_into_struct_is_followed(tmp_path):
    _write(tmp_path, {"a.go": GO_STRUCT})
    assert _rules(tmp_path, "path-traversal") == [("path-traversal", 7)]


def test_go_json_decoder_into_map_is_followed(tmp_path):
    src = GO_STRUCT.replace("var d Req", "var d map[string]string").replace("d.P", 'd["p"]')
    _write(tmp_path, {"a.go": src})
    assert _rules(tmp_path, "path-traversal") == [("path-traversal", 7)]


def test_go_json_unmarshal_of_the_body_is_followed(tmp_path):
    src = ('package main\nimport ("net/http"; "os"; "path"; "encoding/json"; "io")\ntype Req struct{ P string }\n'
           'func h(w http.ResponseWriter, r *http.Request) {\n\tbody, _ := io.ReadAll(r.Body)\n\tvar d Req\n\tjson.Unmarshal(body, &d)\n'
           '\tb, _ := os.ReadFile(path.Join("/srv", d.P))\n\tw.Write(b)\n}\n')
    _write(tmp_path, {"a.go": src})
    assert _rules(tmp_path, "path-traversal") == [("path-traversal", 8)]


def test_go_gin_should_bind_json_is_followed(tmp_path):
    src = ('package main\nimport ("os"; "path"; "github.com/gin-gonic/gin")\ntype Req struct{ P string }\n'
           'func h(c *gin.Context) {\n\tvar d Req\n\tif err := c.ShouldBindJSON(&d); err != nil { return }\n'
           '\tb, _ := os.ReadFile(path.Join("/srv", d.P))\n\tc.String(200, string(b))\n}\n')
    _write(tmp_path, {"a.go": src})
    assert _rules(tmp_path, "path-traversal") == [("path-traversal", 7)]


def test_go_custom_context_type_that_wraps_the_request(tmp_path):
    files = {
        "server/ctx.go": 'package server\nimport "net/http"\ntype Ctx struct { Request *http.Request; ResponseWriter http.ResponseWriter }\n'
                         'func (c *Ctx) Decode(v interface{}) error { return nil }\n',
        "api/h.go": ('package api\nimport ("os"; "path"; "example.com/app/server")\ntype Req struct{ P string }\n'
                     'func Show(gp *server.Ctx) error {\n\tvar d Req\n\tif err := gp.Decode(&d); err != nil { return err }\n'
                     '\t_, err := os.ReadFile(path.Join("/srv", d.P))\n\treturn err\n}\n'),
    }
    _write(tmp_path, files)
    assert _rules(tmp_path, "path-traversal") == [("path-traversal", 7)]


def test_go_decoding_a_file_not_the_request_stays_quiet(tmp_path):
    src = ('package main\nimport ("net/http"; "os"; "path"; "encoding/json")\ntype Req struct{ P string }\n'
           'func h(w http.ResponseWriter, r *http.Request) {\n\tf, _ := os.Open("/etc/app.json")\n\tvar d Req\n\tjson.NewDecoder(f).Decode(&d)\n'
           '\tb, _ := os.ReadFile(path.Join("/srv", d.P))\n\tw.Write(b)\n}\n')
    _write(tmp_path, {"a.go": src})
    assert _rules(tmp_path, "path-traversal") == []


def test_go_struct_without_a_request_field_is_not_a_request_context(tmp_path):
    files = {
        "server/ctx.go": 'package server\ntype Cfg struct { Name string }\nfunc (c *Cfg) Decode(v interface{}) error { return nil }\n',
        "api/h.go": ('package api\nimport ("os"; "path"; "example.com/app/server")\ntype Req struct{ P string }\n'
                     'func Show(cfg *server.Cfg) error {\n\tvar d Req\n\tcfg.Decode(&d)\n'
                     '\t_, err := os.ReadFile(path.Join("/srv", d.P))\n\treturn err\n}\n'),
    }
    _write(tmp_path, files)
    assert _rules(tmp_path, "path-traversal") == []


# ---- JS: promisified child_process functions are command sinks

JS_PROMISIFIED = ('const { exec } = require("child_process");\nconst { promisify } = require("util");\nconst execAsync = promisify(exec);\n'
                  'const express = require("express"); const app = express();\n'
                  'app.post("/x", async (req, res) => { const { stdout } = await execAsync(`git init -b "${req.body.branch}"`); res.send("ok"); });\n')


def test_js_promisified_exec_is_a_command_sink(tmp_path):
    _write(tmp_path, {"a.js": JS_PROMISIFIED})
    assert _rules(tmp_path, "command-injection") == [("command-injection", 5)]


def test_js_util_promisify_of_a_module_member(tmp_path):
    src = ('const cp = require("child_process"); const util = require("util");\nconst run = util.promisify(cp.execFile);\n'
           'const express = require("express"); const app = express();\n'
           'app.get("/x", async (req, res) => { await run("sh", ["-c", req.query.c]); res.send("ok"); });\n')
    _write(tmp_path, {"a.js": src})
    assert _rules(tmp_path, "command-injection") == [("command-injection", 4)]


def test_ts_import_style_promisified_exec(tmp_path):
    src = ('import { exec } from "node:child_process";\nimport { promisify } from "node:util";\nconst execAsync = promisify(exec);\n'
           'import express from "express"; const app = express();\n'
           'app.post("/x", async (req, res) => { await execAsync("ls " + req.body.dir); res.send("ok"); });\n')
    _write(tmp_path, {"a.ts": src})
    assert _rules(tmp_path, "command-injection") == [("command-injection", 5)]


def test_js_promisify_of_something_else_is_not_a_sink(tmp_path):
    src = ('const fs = require("fs"); const { promisify } = require("util");\nconst readAsync = promisify(fs.readFile);\n'
           'const wait = promisify((ms, cb) => setTimeout(cb, ms));\nconst express = require("express"); const app = express();\n'
           'app.get("/x", async (req, res) => { await wait(Number(req.query.ms)); res.send("ok"); });\n')
    _write(tmp_path, {"a.js": src})
    assert _rules(tmp_path, "command-injection") == []


# ---- JS/TS: MCP tool handlers receive attacker-influenced arguments

def test_mcp_tool_handler_arguments_reach_a_command(tmp_path):
    src = ('import { exec } from "child_process"; import { promisify } from "util";\nconst execAsync = promisify(exec);\n'
           'export function register(server: any) {\n  server.tool("git_init", "init", {}, async (args: any) => {\n'
           '    await execAsync(`git init -b "${args.branch}"`);\n    return { content: [] };\n  });\n}\n')
    _write(tmp_path, {"t.ts": src})
    assert _rules(tmp_path, "command-injection") == [("command-injection", 5)]


def test_mcp_tool_arguments_flow_into_another_file(tmp_path):
    logic = ('import { exec } from "child_process"; import { promisify } from "util";\nconst execAsync = promisify(exec);\n'
             'export async function run(input: { dir: string }) {\n  await execAsync(`ls ${input.dir}`);\n}\n')
    reg = ('import { run } from "./logic";\nexport function register(server: any) {\n'
           '  server.registerTool("ls", { description: "x" }, async (params: any) => { await run(params); return { content: [] }; });\n}\n')
    _write(tmp_path, {"logic.ts": logic, "reg.ts": reg})
    assert [r for r in _rules(tmp_path, "command-injection")] == [("command-injection", 4)]


def test_mcp_tool_that_ignores_its_arguments_is_quiet(tmp_path):
    src = ('import { exec } from "child_process"; import { promisify } from "util";\nconst execAsync = promisify(exec);\n'
           'export function register(server: any) {\n  server.tool("status", "s", {}, async (args: any) => {\n'
           '    await execAsync("git status");\n    return { content: [] };\n  });\n}\n')
    _write(tmp_path, {"t.ts": src})
    assert _rules(tmp_path, "command-injection") == []


def test_mcp_tool_registered_with_a_type_argument(tmp_path):
    logic = ('import { exec } from "child_process"; import { promisify } from "util";\nconst execAsync = promisify(exec);\n'
             'export async function run(input: any) {\n  await execAsync(`ls ${input.dir}`);\n}\n')
    reg = ('import { run } from "./logic";\nexport function register(server: any) {\n'
           '  server.tool<typeof Schema>("ls", "d", Schema, async (validatedArgs: any) => { return await run({ ...validatedArgs, path: "p" }); });\n}\n')
    _write(tmp_path, {"logic.ts": logic, "reg.ts": reg})
    assert _rules(tmp_path, "command-injection") == [("command-injection", 4)]


def test_ts_esm_import_with_js_extension_resolves_to_the_ts_file(tmp_path):
    logic = ('import { exec } from "child_process"; import { promisify } from "util";\nconst execAsync = promisify(exec);\n'
             'export async function run(input: any) {\n  await execAsync(`ls ${input.dir}`);\n}\n')
    reg = ('import { run } from "./logic.js";\nexport function register(server: any) {\n'
           '  server.registerTool("ls", {}, async (args: any) => { return await run(args); });\n}\n')
    _write(tmp_path, {"logic.ts": logic, "reg.ts": reg})
    assert _rules(tmp_path, "command-injection") == [("command-injection", 4)]


def test_ts_esm_js_extension_does_not_override_a_real_js_file(tmp_path):
    safe = 'export async function run(input) { return 1; }\n'
    ts = ('import { exec } from "child_process";\nexport async function run(input: any) { exec(`ls ${input.dir}`); }\n')
    reg = ('import { run } from "./logic.js";\nexport function register(server: any) {\n'
           '  server.registerTool("ls", {}, async (args: any) => { return await run(args); });\n}\n')
    _write(tmp_path, {"logic.js": safe, "logic.ts": ts, "reg.ts": reg})
    # the real logic.js exists, so that is the file the import names; its run() runs no command
    assert _rules(tmp_path, "command-injection") == []


# ---- Ruby: the Rack env is a request source

def test_rack_env_path_info_reaches_a_file_read(tmp_path):
    src = ('class App\n  def call(env)\n    path = env["PATH_INFO"]\n    File.read(File.join("/srv", path))\n  end\nend\n')
    _write(tmp_path, {"app.rb": src})
    assert _rules(tmp_path, "path-traversal") == [("path-traversal", 4)]


def test_rack_env_with_a_constant_key(tmp_path):
    src = ('PATH_INFO = "PATH_INFO"\nclass App\n  def call(env)\n    path = env[PATH_INFO]\n    File.read(File.join("/srv", path))\n  end\nend\n'
           'module Rack\n  class Other\n    def get(env)\n      p = Rack::Utils.unescape_path(env[Rack::PATH_INFO])\n      File.read(File.join("/srv", p))\n    end\n  end\nend\n')
    _write(tmp_path, {"app.rb": src})
    assert _rules(tmp_path, "path-traversal") == [("path-traversal", 5), ("path-traversal", 12)]


def test_rack_env_keys_that_are_not_request_data_stay_quiet(tmp_path):
    src = ('class App\n  def call(env)\n    path = env["rack.errors"]\n    File.read(File.join("/srv", path.to_s))\n  end\nend\n')
    _write(tmp_path, {"app.rb": src})
    assert _rules(tmp_path, "path-traversal") == []


def test_a_hash_that_is_not_named_env_stays_quiet(tmp_path):
    src = ('class Config\n  def load(settings)\n    name = settings["PATH_INFO"]\n    File.read(File.join("/srv", name))\n  end\nend\n')
    _write(tmp_path, {"cfg.rb": src})
    assert _rules(tmp_path, "path-traversal") == []


def test_send_package_path_is_a_traversal_sink(tmp_path):
    (tmp_path / "a.js").write_text(
        "const send = require('send')\n"
        "const app = require('express')()\n"
        "app.get('/', (req, res) => {\n"
        "  send(req, req.url).pipe(res)\n"
        "})\n")
    assert _rules(tmp_path, "path-traversal") == [("path-traversal", 4)]


def test_send_package_constant_path_is_quiet(tmp_path):
    (tmp_path / "a.js").write_text(
        "const send = require('send')\n"
        "const app = require('express')()\n"
        "app.get('/', (req, res) => {\n"
        "  send(req, '/static/index.html').pipe(res)\n"
        "})\n")
    assert _rules(tmp_path, "path-traversal") == []


def test_unrelated_send_function_is_not_a_sink(tmp_path):
    (tmp_path / "a.js").write_text(
        "function send(a, b) { return a + b }\n"
        "const app = require('express')()\n"
        "app.get('/', (req, res) => {\n"
        "  send(req, req.url)\n"
        "})\n")
    assert _rules(tmp_path, "path-traversal") == []
