from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, files):
    for name, code in files.items():
        (tmp_path / name).write_text(code)
    return sorted((e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "jaxrs")


RESOURCE = """import jakarta.ws.rs.*;
@Path("/users")
public class Users {
    @GET @Path("/{id}")
    public String get() { return ""; }
    @POST
    public void create() {}
    @DELETE @Path(value = "/{id}")
    public void remove() {}
}
"""


def test_class_path_method_path_and_verbs(tmp_path):
    assert _routes(tmp_path, {"Users.java": RESOURCE}) == [
        ("DELETE", "/users/{id}"), ("GET", "/users/{id}"), ("POST", "/users"),
    ]


def test_application_path_in_another_file_is_prepended(tmp_path):
    app = 'import jakarta.ws.rs.ApplicationPath;\nimport jakarta.ws.rs.core.Application;\n@ApplicationPath("/api")\npublic class App extends Application {}\n'
    got = _routes(tmp_path, {"Users.java": RESOURCE, "App.java": app})
    assert ("GET", "/api/users/{id}") in got and ("POST", "/api/users") in got


def test_method_without_a_path_on_a_class_without_one_is_the_root(tmp_path):
    code = "import javax.ws.rs.*;\npublic class Root {\n    @GET\n    public String get() { return \"\"; }\n}\n"
    assert _routes(tmp_path, {"Root.java": code}) == [("GET", "/")]


def test_java_without_ws_rs_is_ignored(tmp_path):
    code = '@Path("/x")\npublic class A {\n    @GET\n    public String g() { return ""; }\n}\n'
    assert _routes(tmp_path, {"A.java": code}) == []
