"""Flask blueprint prefixes, nested blueprints, factory-built blueprints and add_url_rule (end to end, tmp repos)."""

from pathlib import Path

from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    repo = tmp_path / "r"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return {(e["method"], e["path"]) for e in map_api_endpoints(repo)["endpoints"]}


def test_blueprint_constructor_url_prefix_applies(tmp_path):
    got = _routes(tmp_path, {
        "views.py": 'from flask import Blueprint\nbp = Blueprint("v", __name__, url_prefix="/api")\n@bp.route("/x")\ndef x(): ...\n',
    })
    assert ("GET", "/api/x") in got


def test_register_blueprint_url_prefix_applies_across_files(tmp_path):
    got = _routes(tmp_path, {
        "app.py": 'from flask import Flask\nfrom views import bp\napp = Flask(__name__)\napp.register_blueprint(bp, url_prefix="/v1")\n',
        "views.py": 'from flask import Blueprint\nbp = Blueprint("v", __name__)\n@bp.route("/x", methods=["POST"])\ndef x(): ...\n',
    })
    assert ("POST", "/v1/x") in got
    assert ("POST", "/x") not in got


def test_register_time_prefix_overrides_the_constructor_prefix(tmp_path):
    got = _routes(tmp_path, {
        "app.py": 'from views import bp\nfrom flask import Flask\napp = Flask(__name__)\napp.register_blueprint(bp, url_prefix="/new")\n',
        "views.py": 'from flask import Blueprint\nbp = Blueprint("v", __name__, url_prefix="/old")\n@bp.get("/x")\ndef x(): ...\n',
    })
    assert ("GET", "/new/x") in got
    assert ("GET", "/old/x") not in got and ("GET", "/new/old/x") not in got


def test_blueprint_registered_without_prefix_uses_its_constructor_prefix(tmp_path):
    got = _routes(tmp_path, {
        "app.py": 'from views import bp\nfrom flask import Flask\napp = Flask(__name__)\napp.register_blueprint(bp)\n',
        "views.py": 'from flask import Blueprint\nbp = Blueprint("v", __name__, url_prefix="/admin")\n@bp.get("/users")\ndef u(): ...\n',
    })
    assert ("GET", "/admin/users") in got


def test_nested_blueprints_compose_prefixes(tmp_path):
    got = _routes(tmp_path, {
        "app.py": 'from flask import Flask\nfrom admin import admin_bp\napp = Flask(__name__)\napp.register_blueprint(admin_bp)\n',
        "admin.py": (
            'from flask import Blueprint\n'
            'admin_bp = Blueprint("admin", __name__, url_prefix="/admin")\n'
            'rep = Blueprint("rep", __name__, url_prefix="/reports")\n'
            '@rep.get("/daily")\ndef d(): ...\n'
            'admin_bp.register_blueprint(rep)\n'
        ),
    })
    assert ("GET", "/admin/reports/daily") in got


def test_blueprint_built_in_a_factory_function(tmp_path):
    got = _routes(tmp_path, {
        "app.py": 'from flask import Flask\nfrom shop import create_shop\napp = Flask(__name__)\napp.register_blueprint(create_shop(), url_prefix="/shop")\n',
        "shop.py": 'from flask import Blueprint\ndef create_shop():\n    bp = Blueprint("shop", __name__)\n    @bp.route("/p")\n    def p(): ...\n    return bp\n',
    })
    assert ("GET", "/shop/p") in got


def test_blueprint_mounted_twice_is_reachable_at_both_prefixes(tmp_path):
    got = _routes(tmp_path, {
        "app.py": 'from flask import Flask\nfrom v import bp\napp = Flask(__name__)\napp.register_blueprint(bp, url_prefix="/a")\napp.register_blueprint(bp, url_prefix="/b", name="b2")\n',
        "v.py": 'from flask import Blueprint\nbp = Blueprint("v", __name__)\n@bp.get("/x")\ndef x(): ...\n',
    })
    assert {("GET", "/a/x"), ("GET", "/b/x")} <= got


def test_add_url_rule_with_a_function_and_default_get(tmp_path):
    got = _routes(tmp_path, {"app.py": 'from flask import Flask\napp = Flask(__name__)\ndef ping(): ...\napp.add_url_rule("/ping", "ping", ping)\n'})
    assert ("GET", "/ping") in got


def test_add_url_rule_with_explicit_methods_and_rule_keyword(tmp_path):
    got = _routes(tmp_path, {"app.py": 'from flask import Flask\napp = Flask(__name__)\ndef f(): ...\napp.add_url_rule(rule="/f", view_func=f, methods=["GET", "POST"])\n'})
    assert {("GET", "/f"), ("POST", "/f")} <= got


def test_add_url_rule_on_a_blueprint_gets_the_prefix(tmp_path):
    got = _routes(tmp_path, {
        "app.py": 'from flask import Flask\nfrom v import bp\napp = Flask(__name__)\napp.register_blueprint(bp, url_prefix="/p")\n',
        "v.py": 'from flask import Blueprint\nbp = Blueprint("v", __name__)\ndef f(): ...\nbp.add_url_rule("/f", view_func=f)\n',
    })
    assert ("GET", "/p/f") in got


def test_method_view_methods_come_from_the_class_when_not_given(tmp_path):
    got = _routes(tmp_path, {"app.py": (
        'from flask import Flask\nfrom flask.views import MethodView\napp = Flask(__name__)\n'
        'class API(MethodView):\n    def get(self): ...\n    def post(self): ...\n'
        'app.add_url_rule("/api", view_func=API.as_view("api"))\n'
    )})
    assert {("GET", "/api"), ("POST", "/api")} <= got
    assert ("DELETE", "/api") not in got


def test_method_view_assigned_to_a_variable_first(tmp_path):
    got = _routes(tmp_path, {"app.py": (
        'from flask import Flask\nfrom flask.views import MethodView\napp = Flask(__name__)\n'
        'class API(MethodView):\n    def get(self): ...\n'
        'v = API.as_view("api")\napp.add_url_rule("/api", view_func=v, methods=["GET"])\n'
    )})
    assert ("GET", "/api") in got


def test_an_unregistered_blueprint_keeps_its_constructor_prefix(tmp_path):
    got = _routes(tmp_path, {"v.py": 'from flask import Blueprint\nbp = Blueprint("v", __name__, url_prefix="/solo")\n@bp.get("/x")\ndef x(): ...\n'})
    assert ("GET", "/solo/x") in got


def test_a_non_literal_prefix_is_flagged_unresolved_not_guessed(tmp_path):
    repo = tmp_path / "r"
    files = {
        "app.py": 'from flask import Flask\nfrom v import bp\nimport config\napp = Flask(__name__)\napp.register_blueprint(bp, url_prefix=config.PREFIX)\n',
        "v.py": 'from flask import Blueprint\nbp = Blueprint("v", __name__)\n@bp.get("/x")\ndef x(): ...\n',
    }
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    found = [e for e in map_api_endpoints(repo)["endpoints"] if e["path"].endswith("/x")]
    assert found and all(e["unresolved"] for e in found)  # the prefix is unknown: flagged, never presented as exact


def test_aliased_blueprint_imported_inside_an_app_factory(tmp_path):
    # The Flask tutorial layout: blueprints are imported inside create_app() under an alias.
    got = _routes(tmp_path, {
        "app/__init__.py": (
            "from flask import Flask\n"
            "def create_app():\n"
            "    app = Flask(__name__)\n"
            "    from app.auth import bp as auth_bp\n"
            "    app.register_blueprint(auth_bp, url_prefix='/auth')\n"
            "    from app.api import bp as api_bp\n"
            "    app.register_blueprint(api_bp, url_prefix='/api')\n"
            "    return app\n"
        ),
        "app/auth/__init__.py": "from flask import Blueprint\nbp = Blueprint('auth', __name__)\nfrom app.auth import routes\n",
        "app/auth/routes.py": "from app.auth import bp\n@bp.route('/login', methods=['GET', 'POST'])\ndef login(): ...\n",
        "app/api/__init__.py": "from flask import Blueprint\nbp = Blueprint('api', __name__)\nfrom app.api import users\n",
        "app/api/users.py": "from app.api import bp\n@bp.route('/users/<int:id>', methods=['GET'])\ndef get_user(id): ...\n",
    })
    assert {("GET", "/auth/login"), ("POST", "/auth/login"), ("GET", "/api/users/<int:id>")} <= got
    assert ("GET", "/login") not in got


def test_module_attribute_blueprint_with_an_aliased_package(tmp_path):
    got = _routes(tmp_path, {
        "app.py": "from flask import Flask\nfrom pkg import users as u\napp = Flask(__name__)\napp.register_blueprint(u.bp, url_prefix='/u')\n",
        "pkg/__init__.py": "",
        "pkg/users.py": "from flask import Blueprint\nbp = Blueprint('u', __name__)\n@bp.get('/me')\ndef me(): ...\n",
    })
    assert ("GET", "/u/me") in got
