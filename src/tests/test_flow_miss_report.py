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
