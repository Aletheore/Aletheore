"""Go discover and validate modes: request data is followed to sinks across functions and packages, and the
near misses seen on govwa, gogs and go-test-bench are not reported."""

import pytest

from aletheore.flow.discover_go import discover_go, validate_go_finding


def run(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return discover_go(tmp_path)


def kinds(found):
    return sorted({f["rule_id"].split(".")[-1] for f in found})


def handler(body, extra_imports=""):
    return ('package main\n\nimport (\n\t"database/sql"\n\t"fmt"\n\t"net/http"\n\t"os"\n\t"os/exec"\n\t"path/filepath"\n\t"strconv"\n\t"strings"\n' + extra_imports +
            ')\n\nvar db *sql.DB\n\nfunc h(w http.ResponseWriter, r *http.Request) {\n' + body + '\n}\n')


TAINTED = {
    "sprintf query": ('id := r.URL.Query().Get("id")\nq := fmt.Sprintf("SELECT * FROM t WHERE id = %s", id)\ndb.Query(q)', "sql-injection"),
    "concatenated query": ('db.Exec("DELETE FROM t WHERE n = \'" + r.FormValue("n") + "\'")', "sql-injection"),
    "query with context": ('db.QueryContext(r.Context(), "SELECT "+r.FormValue("n"))', "sql-injection"),
    "exec command": ('exec.Command(r.FormValue("c")).Run()', "command-injection"),
    "shell -c": ('exec.Command("sh", "-c", "echo "+r.FormValue("c")).Run()', "command-injection"),
    "open": ('os.Open(r.URL.Query().Get("f"))', "path-traversal"),
    "readfile joined": ('os.ReadFile(filepath.Join("/data", r.FormValue("f")))', "path-traversal"),
    "serve file": ('http.ServeFile(w, r, r.URL.Path)', "path-traversal"),
    "fprintf": ('fmt.Fprintf(w, "<b>%s</b>", r.FormValue("n"))', "xss"),
    "write": ('w.Write([]byte("<b>" + r.FormValue("n") + "</b>"))', "xss"),
    "redirect": ('http.Redirect(w, r, r.FormValue("next"), 302)', "open-redirect"),
    "ssrf": ('http.Get(r.FormValue("u"))', "ssrf"),
    "header value": ('exec.Command(r.Header.Get("X-Cmd")).Run()', "command-injection"),
    "cookie value": ('c, _ := r.Cookie("n")\nos.Open(c.Value)', "path-traversal"),
    "map value": ('m := map[string]string{"k": r.FormValue("v")}\nexec.Command(m["k"]).Run()', "command-injection"),
    "append": ('parts := []string{"a"}\nparts = append(parts, r.FormValue("b"))\nexec.Command(strings.Join(parts, " ")).Run()', "command-injection"),
    "branch": ('n := "x"\nif r.FormValue("m") != "" {\n n = r.FormValue("a")\n}\nexec.Command(n).Run()', "command-injection"),
    "closure": ('f := func(s string) string { return s }\nexec.Command(f(r.FormValue("c"))).Run()', "command-injection"),
}


@pytest.mark.parametrize("name", sorted(TAINTED))
def test_tainted_flows_are_reported(tmp_path, name):
    body, expected = TAINTED[name]
    assert expected in kinds(run(tmp_path, {"main.go": handler(body)})), name


SAFE = {
    "constant query": 'db.Query("SELECT 1")',
    "number first": 'n, _ := strconv.Atoi(r.FormValue("n"))\ndb.Query("SELECT " + strconv.Itoa(n))',
    "constant command": 'exec.Command("echo", r.FormValue("c")).Run()',
    "overwritten": 'n := r.FormValue("n")\nn = "fixed"\nexec.Command(n).Run()',
    "base name": 'os.Open(filepath.Base(r.FormValue("f")))',
    "escaped output": 'fmt.Fprintf(w, "<b>%s</b>", html.EscapeString(r.FormValue("n")))',
    "map safe key": 'm := map[string]string{"a": "x", "b": r.FormValue("v")}\nexec.Command(m["a"]).Run()',
    "other writer": 'fmt.Fprintf(os.Stderr, "%s", r.FormValue("n"))',
    "plain text response": 'w.Header().Set("Content-Type", "text/plain")\nfmt.Fprintf(w, "%s", r.FormValue("n"))',
    "stat is not access": 'os.Stat(r.FormValue("f"))',
    "dead branch": 'n := "safe"\nif false {\n n = r.FormValue("a")\n}\nexec.Command(n).Run()',
}


@pytest.mark.parametrize("name", sorted(SAFE))
def test_safe_flows_are_not_reported(tmp_path, name):
    imports = '\t"html"\n' if "html." in SAFE[name] else ""
    assert run(tmp_path, {"main.go": handler(SAFE[name], imports)}) == [], name


def test_a_prepared_statements_parameters_are_not_sql_text(tmp_path):
    body = 'stmt, _ := db.Prepare("SELECT * FROM t WHERE a = ?")\nstmt.QueryRow(r.FormValue("a"))'
    assert run(tmp_path, {"main.go": handler(body)}) == []


def test_a_prepared_statement_built_from_request_text_is_still_a_finding(tmp_path):
    body = 'db.Prepare("SELECT * FROM t WHERE a = " + r.FormValue("a"))'
    assert "sql-injection" in kinds(run(tmp_path, {"main.go": handler(body)}))


def test_command_line_arguments_are_not_request_data(tmp_path):
    src = 'package main\n\nimport (\n\t"os"\n\t"os/exec"\n)\n\nfunc main() {\n\tos.Create(os.Args[1])\n\texec.Command(os.Args[0], "x").Run()\n}\n'
    assert run(tmp_path, {"main.go": src}) == []


def test_a_method_on_a_record_fetched_by_a_tainted_key_is_not_a_tainted_string(tmp_path):
    src = handler('rec, _ := lookup(r.FormValue("id"))\nos.Open(rec.Path())') + 'func lookup(id string) (*Rec, error) { return nil, nil }\ntype Rec struct{}\nfunc (r *Rec) Path() string { return "" }\n'
    assert run(tmp_path, {"main.go": src}) == []


def test_a_function_in_another_file_of_the_package_is_followed(tmp_path):
    found = run(tmp_path, {
        "main.go": handler('runIt(r.FormValue("c"))'),
        "util.go": 'package main\n\nimport "os/exec"\n\nfunc runIt(c string) {\n\texec.Command(c).Run()\n}\n',
    })
    assert kinds(found) == ["command-injection"] and found[0]["path"] == "util.go"


def test_a_function_in_another_package_is_followed_through_the_import(tmp_path):
    found = run(tmp_path, {
        "go.mod": "module example.com/app\n\ngo 1.21\n",
        "main.go": handler('util.Run(r.FormValue("c"))', '\t"example.com/app/internal/util"\n'),
        "internal/util/util.go": 'package util\n\nimport "os/exec"\n\nfunc Run(c string) {\n\texec.Command(c).Run()\n}\n',
    })
    assert kinds(found) == ["command-injection"] and found[0]["path"].endswith("util.go")


def test_a_struct_method_is_followed_and_handlers_registered_inline_are_run(tmp_path):
    src = ('package main\n\nimport (\n\t"net/http"\n\t"os/exec"\n)\n\ntype S struct{}\n\nfunc (s *S) run(c string) { exec.Command(c).Run() }\n\n'
           'func main() {\n\ts := &S{}\n\thttp.HandleFunc("/x", func(w http.ResponseWriter, r *http.Request) {\n\t\ts.run(r.FormValue("c"))\n\t})\n}\n')
    assert kinds(run(tmp_path, {"main.go": src})) == ["command-injection"]


def test_gin_context_inputs_are_request_data(tmp_path):
    src = 'package main\n\nimport (\n\t"os/exec"\n\t"github.com/gin-gonic/gin"\n)\n\nfunc h(c *gin.Context) {\n\texec.Command(c.Query("cmd")).Run()\n}\n'
    assert kinds(run(tmp_path, {"main.go": src})) == ["command-injection"]


def test_validate_mode_suppresses_a_constant_command_and_keeps_a_tainted_one(tmp_path):
    src = handler('exec.Command("echo", r.FormValue("c")).Run()\nexec.Command(r.FormValue("d")).Run()')
    (tmp_path / "main.go").write_text(src)
    lines = src.splitlines()

    def verdict(match):
        ln = next(i for i, l in enumerate(lines, 1) if match in l)
        col = lines[ln - 1].index("exec.Command") + 1
        return validate_go_finding(tmp_path, "main.go", (ln, col), (ln, len(lines[ln - 1])), {78}).kind

    assert verdict('"echo"') in ("safe", "unreachable")
    assert verdict('FormValue("d")') == "tainted"
