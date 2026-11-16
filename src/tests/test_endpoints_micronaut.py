from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, name, code):
    (tmp_path / name).write_text(code)
    return sorted((e["method"], e["path"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "micronaut")


def test_java_controller_prefix_and_verbs(tmp_path):
    code = """import io.micronaut.http.annotation.*;
@Controller("/users")
public class UserController {
    @Get("/{id}")
    public String get(String id) { return ""; }
    @Post
    public void create() {}
    @Delete(uri = "/{id}")
    public void remove(String id) {}
}
"""
    assert _routes(tmp_path, "UserController.java", code) == [
        ("DELETE", "/users/{id}"), ("GET", "/users/{id}"), ("POST", "/users"),
    ]


def test_kotlin_controller_and_several_uris(tmp_path):
    code = """import io.micronaut.http.annotation.*
@Controller("/items")
open class ItemController {
    @Get("/{id}")
    suspend fun one(id: Long): String = ""
    @Put(uris = ["/a", "/b"])
    fun many() = 1
}
"""
    assert _routes(tmp_path, "ItemController.kt", code) == [
        ("GET", "/items/{id}"), ("PUT", "/items/a"), ("PUT", "/items/b"),
    ]


def test_controller_without_a_path_is_the_root(tmp_path):
    code = "import io.micronaut.http.annotation.*;\n@Controller\npublic class Root {\n    @Get\n    public String index() { return \"\"; }\n}\n"
    assert _routes(tmp_path, "Root.java", code) == [("GET", "/")]


def test_files_without_micronaut_are_ignored(tmp_path):
    code = '@Controller("/x")\npublic class A {\n    @Get("/y")\n    public String g() { return ""; }\n}\n'
    assert _routes(tmp_path, "A.java", code) == []


def test_internal_kotlin_functions_and_nested_generic_java_return_types(tmp_path):
    kotlin = 'import io.micronaut.http.annotation.*\n@Controller("/k")\nclass K {\n    @Get("/a")\n    internal fun index() = 1\n    @Get("/b")\n    private suspend inline fun other() = 2\n}\n'
    java = 'import io.micronaut.http.annotation.*;\n@Controller("/j")\npublic class J {\n    @Get("/c")\n    public Map<String, List<User>> list() { return null; }\n}\n'
    assert _routes(tmp_path, "K.kt", kotlin) == [("GET", "/k/a"), ("GET", "/k/b")]
    assert _routes(tmp_path, "J.java", java) == [("GET", "/j/c"), ("GET", "/k/a"), ("GET", "/k/b")]


def test_other_string_attributes_and_placeholders(tmp_path):
    code = """import io.micronaut.http.annotation.*;
@Controller("${api.root:/api}")
public class A {
    @Get(uri = "/users", name = "list-users")
    public String a() { return ""; }
    @Get("${version.path}/x")
    public String b() { return ""; }
}
"""
    (tmp_path / "A.java").write_text(code)
    got = sorted((e["path"], e["unresolved"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "micronaut")
    assert got == [("/api/${version.path}/x", True), ("/api/users", False)]


def test_client_interfaces_and_comments_are_not_routes(tmp_path):
    code = """import io.micronaut.http.annotation.*;
@Controller("/real")
public class C {
    // @Get("/commented")
    /* @Post("/block") */
    @Get("/live")
    public String live() { return ""; }
}
@Client("/other")
interface Api {
    @Get("/remote")
    String remote();
}
"""
    assert _routes(tmp_path, "C.java", code) == [("GET", "/real/live")]
