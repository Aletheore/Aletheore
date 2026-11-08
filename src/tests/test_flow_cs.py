"""C# discover and validate modes: request data is followed to sinks across methods and classes, constants and
parameter binding are recognised as safe, and anything the engine cannot model is kept."""

import pytest

from aletheore.flow.discover_cs import discover_cs, validate_cs_finding


def run(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return discover_cs(tmp_path)


def kinds(found):
    return sorted({f["rule_id"].split(".")[-1] for f in found})


def action(body, members=""):
    return "using System;\npublic class HomeController : Controller {\n" + members + "\n    public IActionResult Act(string name) {\n" + body + "\n        return Ok();\n    }\n}\n"


def page(body):
    return "public class P : System.Web.UI.Page {\n    protected void Page_Load(object s, EventArgs e) {\n" + body + "\n    }\n}\n"


TAINTED = {
    "concat sql": (action('var c = new SqlCommand("SELECT * FROM t WHERE n=\'" + name + "\'", conn);'), "sql-injection"),
    "interpolated sql": (action('var c = new SqlCommand($"SELECT * FROM t WHERE n=\'{name}\'", conn);'), "sql-injection"),
    "string.Format sql": (action('var c = new SqlCommand(string.Format("SELECT * FROM t WHERE n=\'{0}\'", name), conn);'), "sql-injection"),
    "CommandText assign": (action('var c = new SqlCommand(); c.CommandText = "SELECT " + name;'), "sql-injection"),
    "efcore raw": (action('db.Users.FromSqlRaw("SELECT * FROM u WHERE n=\'" + name + "\'");'), "sql-injection"),
    "query string": (page('var id = Request.QueryString["id"]; var c = new SqlCommand("select " + id);'), "sql-injection"),
    "process start": (action('Process.Start("cmd.exe", "/c " + name);'), "command-injection"),
    "file read": (action('File.ReadAllText("/data/" + name);'), "path-traversal"),
    "response write": (page('Response.Write(Request["n"]);'), "xss"),
    "redirect": (page('Response.Redirect(Request.QueryString["u"]);'), "open-redirect"),
    "webclient": (action('new WebClient().DownloadString(name);'), "ssrf"),
    "xpath": (action('doc.SelectNodes("//u[n=\'" + name + "\']");'), "xpath-injection"),
    "through helper": (action('Run(name);', 'void Run(string x) { var c = new SqlCommand("select " + x); }'), "sql-injection"),
    "through field": (action('this.v = name; Go();', 'string v; void Go() { var c = new SqlCommand("select " + v); }'), "sql-injection"),
    "stringbuilder": (action('var sb = new StringBuilder(); sb.Append("select "); sb.Append(name); var c = new SqlCommand(sb.ToString());'), "sql-injection"),
    "list": (action('var l = new List<string>(); l.Add(name); var c = new SqlCommand("select " + l[0]);'), "sql-injection"),
    "branch join": (action('var q = "select 1"; if (name.Length > 3) q = "select " + name; var c = new SqlCommand(q);'), "sql-injection"),
    "foreach": (action('foreach (var p in new[] { name }) { var c = new SqlCommand("select " + p); }'), "sql-injection"),
}


@pytest.mark.parametrize("case", sorted(TAINTED))
def test_tainted_flows_are_reported(tmp_path, case):
    src, kind = TAINTED[case]
    assert kind in kinds(run(tmp_path, {"A.cs": src}))


SAFE = {
    "const table": action('var c = new SqlCommand("SELECT * FROM " + TBL + " WHERE a=1", conn);', 'const string TBL = "users";'),
    "static readonly": action('var c = new SqlCommand("SELECT * FROM " + T, conn);', 'static readonly string T = "users";'),
    "parameterized": action('var c = new SqlCommand("SELECT * FROM t WHERE n=@n", conn); c.Parameters.AddWithValue("@n", name);'),
    "int parse": action('var id = int.Parse(name); var c = new SqlCommand("select " + id);'),
    "int param": "public class HomeController : Controller {\n public IActionResult Act(int id) { var c = new SqlCommand(\"select \" + id); return Ok(); }\n}\n",
    "html encoded": page('Response.Write(Server.HtmlEncode(Request["n"]));'),
    "constant redirect": page('Response.Redirect("/home");'),
    "dead branch": action('if (false) { var c = new SqlCommand("select " + name); }'),
    "constant folded condition": action('const bool D = false; if (D) { File.ReadAllText(name); }'),
    "filename only": action('File.ReadAllText("/data/" + Path.GetFileName(name));'),
    "constant string ops": action('var q = "SELECT 1".ToLower(); var c = new SqlCommand(q);'),
    "private helper returns const": action('var c = new SqlCommand(Table() + " where 1=1");', 'string Table() { return "t"; }'),
}


@pytest.mark.parametrize("case", sorted(SAFE))
def test_safe_flows_are_quiet(tmp_path, case):
    assert run(tmp_path, {"A.cs": SAFE[case]}) == []


def test_cross_class_constant_and_call(tmp_path):
    files = {
        "Db.cs": 'public static class Db { public const string T = "users"; public static void Run(string q) { var c = new SqlCommand(q); } }\n',
        "C.cs": 'public class XController : Controller { public IActionResult A(string n) { Db.Run("select * from " + Db.T); Db.Run("select " + n); return Ok(); } }\n',
    }
    found = run(tmp_path, files)
    assert [f["line"] for f in found] == [1]
    assert found[0]["path"] == "Db.cs"


def test_opaque_container_is_not_trusted(tmp_path):
    src = action('var l = new List<string>(); l.Add("a"); Mystery(l);\nvar c = new SqlCommand("select " + l[0]);')
    (tmp_path / "A.cs").write_text(src)
    ln, line = next((i, l) for i, l in enumerate(src.splitlines(), 1) if "new SqlCommand" in l)
    assert not validate_cs_finding(tmp_path, "A.cs", (ln, line.index("new") + 1), (ln, len(line)), {89}).droppable


def test_validate_suppresses_const_table_and_keeps_tainted(tmp_path):
    safe = action('var c = new SqlCommand("SELECT * FROM " + TBL, conn);', 'const string TBL = "users";')
    bad = action('var c = new SqlCommand("SELECT * FROM " + name, conn);')
    (tmp_path / "S.cs").write_text(safe)
    (tmp_path / "B.cs").write_text(bad)
    def span(src):
        for i, l in enumerate(src.splitlines(), 1):
            if "new SqlCommand" in l:
                return (i, l.index("new SqlCommand") + 1), (i, len(l))
    v1 = validate_cs_finding(tmp_path, "S.cs", *span(safe), {89})
    v2 = validate_cs_finding(tmp_path, "B.cs", *span(bad), {89})
    assert v1.droppable and not v2.droppable
