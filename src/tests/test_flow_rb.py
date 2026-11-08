"""Ruby discover and validate modes: request data (params, cookies, request) is followed to sinks across methods and
classes, constants and parameter binding are recognised as safe, and anything the engine cannot model is kept."""

import pytest

from aletheore.flow.discover_rb import discover_rb, validate_rb_finding


def run(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return discover_rb(tmp_path)


def kinds(found):
    return sorted({f["rule_id"].split(".")[-1] for f in found})


def ctrl(body, extra=""):
    return "class HomeController < ApplicationController\n" + extra + "\n  def act\n" + body + "\n  end\nend\n"


TAINTED = {
    "interpolated where": (ctrl('User.where("name = \'#{params[:n]}\'")'), "sql-injection"),
    "concat find_by_sql": (ctrl('User.find_by_sql("select " + params[:id])'), "sql-injection"),
    "via local": (ctrl('n = params[:n]\nq = "name = #{n}"\nUser.where(q)'), "sql-injection"),
    "heredoc sql": (ctrl('n = params[:n]\nq = <<~SQL\n  select #{n}\nSQL\nActiveRecord::Base.connection.execute(q)'), "sql-injection"),
    "order": (ctrl('User.order(params[:sort])'), "sql-injection"),
    "system": (ctrl('system("ls #{params[:d]}")'), "command-injection"),
    "backtick": (ctrl('`ls #{params[:d]}`'), "command-injection"),
    "popen": (ctrl('IO.popen("cat " + params[:f])'), "command-injection"),
    "file read": (ctrl('File.read(params[:f])'), "path-traversal"),
    "send_file join": (ctrl('send_file File.join("/data", params[:f])'), "path-traversal"),
    "redirect": (ctrl('redirect_to params[:u]'), "open-redirect"),
    "html_safe": (ctrl('render html: params[:x].html_safe'), "xss"),
    "ssrf": (ctrl('Net::HTTP.get(URI(params[:u]))'), "ssrf"),
    "marshal": (ctrl('Marshal.load(params[:d])'), "insecure-deserialization"),
    "yaml": (ctrl('YAML.load(cookies[:d])'), "insecure-deserialization"),
    "eval": (ctrl('eval(params[:c])'), "code-injection"),
    "constantize": (ctrl('params[:k].constantize.new'), "code-injection"),
    "send": (ctrl('User.send(params[:m])'), "code-injection"),
    "through helper": (ctrl('run(params[:x])', "  def run(x)\n    system(x)\n  end"), "command-injection"),
    "ivar": (ctrl('@v = params[:x]\ngo', "  def go\n    system(@v)\n  end"), "command-injection"),
    "append": (ctrl('q = "select "\nq << params[:x]\nUser.find_by_sql(q)'), "sql-injection"),
    "branch": (ctrl('q = "x"\nq = params[:a] if params[:b]\nUser.find_by_sql(q)'), "sql-injection"),
    "each": (ctrl('[params[:a]].each { |v| system(v) }'), "command-injection"),
    "session": (ctrl('session[:u] = params[:u]'), "trust-boundary"),
    "render file": (ctrl('render file: params[:f]'), "path-traversal"),
    "sinatra": ('require "sinatra"\nget "/x" do\n  system(params[:c])\nend\n', "command-injection"),
}


@pytest.mark.parametrize("case", sorted(TAINTED))
def test_tainted_flows_are_reported(tmp_path, case):
    src, kind = TAINTED[case]
    assert kind in kinds(run(tmp_path, {"app/controllers/home_controller.rb": src}))


SAFE = {
    "hash where": ctrl('User.where(name: params[:n])'),
    "bound where": ctrl('User.where("name = ?", params[:n])'),
    "array where": ctrl('User.where(["name = ?", params[:n]])'),
    "find": ctrl('User.find(params[:id])'),
    "to_i": ctrl('User.where("id = #{params[:id].to_i}")'),
    "const table": ctrl('User.find_by_sql("select * from " + TBL)', '  TBL = "users"'),
    "system args": ctrl('system("ls", params[:d])'),
    "shellescape": ctrl('system("ls " + params[:d].shellescape)'),
    "basename": ctrl('File.read(File.join("/data", File.basename(params[:f])))'),
    "html escaped": ctrl('render html: h(params[:x]).html_safe'),
    "constant redirect": ctrl('redirect_to "/home"'),
    "only_path": ctrl('redirect_to params[:u], only_path: true'),
    "dead branch": ctrl('if false\n  system(params[:c])\nend'),
    "constant folded": ctrl('x = "a"\nUser.find_by_sql("select " + x)'),
    "safe_load": ctrl('YAML.safe_load(params[:d])'),
    "private helper const": ctrl('system(cmd)', '  def cmd\n    "ls"\n  end'),
}


@pytest.mark.parametrize("case", sorted(SAFE))
def test_safe_flows_are_quiet(tmp_path, case):
    assert run(tmp_path, {"app/controllers/home_controller.rb": SAFE[case]}) == []


def test_cross_class(tmp_path):
    files = {
        "lib/runner.rb": 'class Runner\n  def self.go(c)\n    system(c)\n  end\nend\n',
        "app/controllers/c_controller.rb": 'class CController < ApplicationController\n  def a\n    Runner.go("ls")\n    Runner.go(params[:c])\n  end\nend\n',
    }
    found = run(tmp_path, files)
    assert [f["path"] for f in found] == ["lib/runner.rb"]


def test_validate_drops_constant_and_keeps_tainted(tmp_path):
    safe = ctrl('User.find_by_sql("select * from " + TBL)', '  TBL = "users"')
    bad = ctrl('User.find_by_sql("select * from " + params[:t])')
    (tmp_path / "s.rb").write_text(safe)
    (tmp_path / "b.rb").write_text(bad)

    def span(src):
        for i, l in enumerate(src.splitlines(), 1):
            if "find_by_sql" in l:
                return (i, l.index("User") + 1), (i, len(l) + 1)
    assert validate_rb_finding(tmp_path, "s.rb", *span(safe), {89}).droppable
    assert not validate_rb_finding(tmp_path, "b.rb", *span(bad), {89}).droppable


def test_opaque_container_not_trusted(tmp_path):
    src = ctrl('l = ["a"]\nmystery(l)\nUser.find_by_sql("select " + l[0])')
    (tmp_path / "a.rb").write_text(src)
    ln, line = next((i, x) for i, x in enumerate(src.splitlines(), 1) if "find_by_sql" in x)
    assert not validate_rb_finding(tmp_path, "a.rb", (ln, line.index("User") + 1), (ln, len(line) + 1), {89}).droppable


NOISE = {
    "redis exists": ctrl('redis.exists?(params[:k])'),
    "arel": ctrl('User.where(arel_table[:name].lower.in(params[:n]))'),
    "db lookup then session": ctrl('u = User.find_by_token(params[:t])\nsession[:u] = u.id'),
    "request host": ctrl('redirect_to "http://#{request.host}/x"'),
    "allow_other_host false": ctrl('redirect_to params[:u], allow_other_host: false'),
    "env omniauth": ctrl('session[:d] = request.env["omniauth.auth"]'),
}


@pytest.mark.parametrize("case", sorted(NOISE))
def test_known_noise_is_quiet(tmp_path, case):
    assert run(tmp_path, {"app/controllers/home_controller.rb": NOISE[case]}) == []


def test_env_origin_still_reported(tmp_path):
    src = ctrl('redirect_to request.env["omniauth.origin"]')
    assert "open-redirect" in kinds(run(tmp_path, {"app/controllers/home_controller.rb": src}))
