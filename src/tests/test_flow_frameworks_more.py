"""Request sources of more web frameworks: Tornado, Falcon, Streamlit, NestJS, tRPC, chi, gorilla/mux, beego, gRPC, Hanami, Slim,
WordPress hooks, Vert.x, Micronaut, Javalin, Spark and C# minimal APIs. Each vulnerable app must be reported; each control stays quiet."""
import pytest

from aletheore.flow.discover import discover_repo


def _run(tmp_path, files):
    for k, v in files.items():
        (tmp_path / k).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / k).write_text(v)
    return [f['line'] for f in discover_repo(tmp_path) if 'command' in f['rule_id'] or 'path' in f['rule_id']]


APPS = {
    'tornado': {'a.py': "import os, tornado.web\nclass H(tornado.web.RequestHandler):\n    def get(self):\n        os.system('ls '+self.get_argument('d'))\n"},
    'falcon': {'a.py': "import os\nclass R:\n    def on_get(self, req, resp):\n        os.system('ls '+req.get_param('d'))\n"},
    'streamlit': {'a.py': "import os, streamlit as st\nd = st.text_input('x')\nos.system('ls '+d)\n"},
    'nest': {'a.ts': "import {exec} from 'child_process';\n@Controller('x')\nexport class C {\n  @Get()\n  i(@Query('d') d: string) { exec('ls '+d); }\n}\n"},
    'trpc': {'a.ts': "import {exec} from 'child_process';\nexport const r = t.procedure.input(z.object({d:z.string()})).query(({input})=>{exec('ls '+input.d);});\n"},
    'chi': {'a.go': 'package main\nimport ("net/http";"os/exec";"github.com/go-chi/chi/v5")\nfunc h(w http.ResponseWriter, r *http.Request){exec.Command("sh","-c",chi.URLParam(r,"d")).Run()}\nfunc main(){}\n'},
    'mux': {'a.go': 'package main\nimport ("net/http";"os/exec";"github.com/gorilla/mux")\nfunc h(w http.ResponseWriter, r *http.Request){exec.Command("sh","-c",mux.Vars(r)["d"]).Run()}\nfunc main(){}\n'},
    'beego': {'a.go': 'package main\nimport ("os/exec";"github.com/beego/beego/v2/server/web")\ntype C struct{web.Controller}\nfunc (c *C) Get(){exec.Command("sh","-c",c.GetString("d")).Run()}\nfunc main(){}\n'},
    'grpc': {'a.go': 'package main\nimport ("context";"os/exec")\ntype S struct{}\nfunc (s *S) Run(ctx context.Context, req *Req) (*Resp, error){exec.Command("sh","-c",req.Cmd).Run();return nil,nil}\nfunc main(){}\n'},
    'hanami': {'a.rb': "class Show\n  include Hanami::Action\n  def handle(req, res)\n    system('ls ' + req.params[:d])\n  end\nend\n"},
    'slim': {'a.php': "<?php\n$app->get('/', function ($request, $response) { system('ls '.$request->getQueryParams()['d']); });\n"},
    'wordpress': {'a.php': "<?php\nadd_action('wp_ajax_x', function(){ system('ls '.$_POST['d']); });\n"},
    'vertx': {'A.java': 'public class A {\n void r(Router router) { router.get("/").handler(ctx -> { try { Runtime.getRuntime().exec("ls "+ctx.request().getParam("d")); } catch(Exception e){} }); }\n}\n'},
    'micronaut': {'A.java': '@Controller("/x") public class A {\n @Get public void i(@QueryValue String d) throws Exception { Runtime.getRuntime().exec("ls "+d); }\n}\n'},
    'javalin': {'A.java': 'public class A {\n void r(Javalin app) { app.get("/", ctx -> { Runtime.getRuntime().exec("ls "+ctx.queryParam("d")); }); }\n}\n'},
    'spark': {'A.java': 'public class A {\n void r() { Spark.get("/", (req, res) -> { Runtime.getRuntime().exec("ls "+req.queryParams("d")); return ""; }); }\n}\n'},
    'minimal_api': {'A.cs': 'using System.Diagnostics;\napp.MapGet("/", (string d) => { Process.Start("sh", "-c " + d); return ""; });\n'},
}

CONTROLS = {
    'tornado_quoted': {'a.py': "import os, shlex, tornado.web\nclass H(tornado.web.RequestHandler):\n    def get(self):\n        os.system('ls '+shlex.quote(self.get_argument('d')))\n"},
    'javalin_constant': {'A.java': 'public class A {\n void r(Javalin app) { app.get("/", ctx -> { Runtime.getRuntime().exec("ls /tmp"); }); }\n}\n'},
    'nest_undecorated': {'a.ts': "import {exec} from 'child_process';\n@Controller('x')\nexport class C {\n  @Get()\n  i(d: string) { exec('ls '+d); }\n}\n"},
    'chi_constant': {'a.go': 'package main\nimport ("net/http";"os/exec";"github.com/go-chi/chi/v5")\nfunc h(w http.ResponseWriter, r *http.Request){_ = chi.URLParam(r,"d"); exec.Command("sh","-c","ls").Run()}\nfunc main(){}\n'},
    'slim_constant': {'a.php': "<?php\n$app->get('/', function ($request, $response) { system('ls /tmp'); });\n"},
    'minimal_api_int': {'A.cs': 'using System.Diagnostics;\napp.MapGet("/", (int d) => { Process.Start("sh", "-c " + d); return ""; });\n'},
    'grpc_no_context': {'a.go': 'package main\nimport "os/exec"\ntype S struct{}\nfunc (s *S) Run(x int, req *Req) { exec.Command("sh","-c","ls").Run() }\nfunc main(){}\n'},
}



@pytest.mark.parametrize("name", list(APPS))
def test_framework_request_reaches_the_command(tmp_path, name):
    assert _run(tmp_path, APPS[name]), name


@pytest.mark.parametrize("name", list(CONTROLS))
def test_control_stays_quiet(tmp_path, name):
    assert _run(tmp_path, CONTROLS[name]) == [], name
