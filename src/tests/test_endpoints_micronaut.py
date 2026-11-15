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
