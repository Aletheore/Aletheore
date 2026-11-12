"""Minimal-style routes and a few more frameworks: Rust closures (axum, actix, warp), Rack lambdas and Rack::Request, Roda, Laravel route
closures, CodeIgniter, Struts action fields. Each vulnerable app is reported, each control stays quiet."""
import pytest

from aletheore.flow.discover import discover_repo


def _run(tmp_path, files):
    for k, v in files.items():
        (tmp_path / k).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / k).write_text(v)
    return [f['line'] for f in discover_repo(tmp_path) if 'command' in f['rule_id'] or 'path' in f['rule_id']]


APPS = {
    'rust axum closure': {'a.rs': 'use axum::{Router, routing::get, extract::Query};\nfn app() -> Router { Router::new().route("/", get(|Query(q): Query<Q>| async move { std::process::Command::new("sh").arg("-c").arg(&q.d).output().unwrap(); "" })) }\n'},
    'rust axum Path': {'a.rs': 'use axum::extract::Path;\nasync fn h(Path(d): Path<String>) { std::process::Command::new("sh").arg("-c").arg(d).output().unwrap(); }\n'},
    'rust actix Json': {'a.rs': 'use actix_web::{post, web};\n#[post("/")]\nasync fn h(b: web::Json<B>) { std::process::Command::new("sh").arg("-c").arg(&b.d).output().unwrap(); }\n'},
    'rust rocket form': {'a.rs': '#[post("/", data = "<f>")]\nfn h(f: Form<F>) { std::process::Command::new("sh").arg("-c").arg(&f.d).output().unwrap(); }\n'},
    'rust hyper': {'a.rs': 'use hyper::{Request, Body};\nasync fn h(req: Request<Body>) { let q = req.uri().query().unwrap(); std::process::Command::new("sh").arg("-c").arg(q).output().unwrap(); }\n'},
    'rust warp': {'a.rs': 'fn r() { warp::path::param().map(|d: String| { std::process::Command::new("sh").arg("-c").arg(d).output().unwrap(); "" }); }\n'},
    'ruby rack lambda': {'config.ru': "run ->(env) { system('ls ' + Rack::Request.new(env).params['d']); [200, {}, []] }\n"},
    'ruby rack class': {'a.rb': "class App\n  def call(env)\n    req = Rack::Request.new(env)\n    system('ls ' + req.params['d'])\n    [200, {}, []]\n  end\nend\n"},
    'ruby roda': {'a.rb': "class App < Roda\n  route do |r|\n    r.get 'x' do\n      system('ls ' + r.params['d'])\n    end\n  end\nend\n"},
    'ruby rails API': {'app/controllers/a_controller.rb': "class AController < ActionController::API\n  def i\n    system('ls ' + params.require(:d))\n  end\nend\n"},
    'php laravel closure': {'routes/web.php': "<?php\nRoute::get('/', function (Request $r) { system('ls '.$r->input('d')); });\n"},
    'php laravel invokable': {'app/Http/Controllers/A.php': "<?php\nclass A extends Controller {\n  public function __invoke(Request $request) { system('ls '.$request->get('d')); }\n}\n"},
    'php codeigniter': {'app/Controllers/A.php': "<?php\nclass A extends BaseController {\n  public function i() { system('ls '.$this->request->getGet('d')); }\n}\n"},
    'php $_REQUEST fn': {'a.php': "<?php\nfunction h() { return shell_exec('ls '.$_REQUEST['d']); }\nh();\n"},
    'php psr7 handler': {'a.php': "<?php\nclass H implements RequestHandlerInterface {\n  public function handle(ServerRequestInterface $request): ResponseInterface { system('ls '.$request->getQueryParams()['d']); }\n}\n"},
    'struts': {'A.java': 'public class A extends ActionSupport {\n private String d;\n public String execute() throws Exception { Runtime.getRuntime().exec("ls "+d); return SUCCESS; }\n public void setD(String d){this.d=d;}\n}\n'},
}

CONTROLS = {
    'rust_closure_int': {'a.rs': 'use axum::{Router, routing::get, extract::Query};\nfn app() -> Router { Router::new().route("/", get(|| async move { std::process::Command::new("sh").arg("-c").arg("ls").output().unwrap(); "" })) }\n'},
    'ruby_rack_constant': {'config.ru': "run ->(env) { system('ls /tmp'); [200, {}, []] }\n"},
    'laravel_constant': {'routes/web.php': "<?php\nRoute::get('/', function (Request $r) { system('ls /tmp'); });\n"},
    'struts_int_field': {'A.java': 'public class A extends ActionSupport {\n private int d;\n public String execute() throws Exception { Runtime.getRuntime().exec("ls "+d); return SUCCESS; }\n}\n'},
    'codeigniter_constant': {'app/Controllers/A.php': "<?php\nclass A extends BaseController {\n  public function i() { system('ls /tmp'); }\n}\n"},
}



@pytest.mark.parametrize("name", list(APPS))
def test_route_reaches_the_command(tmp_path, name):
    assert _run(tmp_path, APPS[name]), name


@pytest.mark.parametrize("name", list(CONTROLS))
def test_control_stays_quiet(tmp_path, name):
    assert _run(tmp_path, CONTROLS[name]) == [], name


def test_rack_request_built_from_a_rewritten_env_is_not_raw_request_data(tmp_path):
    src = ("class R\n  def call(env)\n    request = Rack::Request.new(env)\n"
           "    fake = Rack::Request.new(env.merge('HTTP_HOST' => 'example.org'))\n"
           "    [301, { 'Location' => fake.url }, []]\n  end\nend\n")
    (tmp_path / "a.rb").write_text(src)
    assert [f for f in discover_repo(tmp_path) if "redirect" in f["rule_id"]] == []
