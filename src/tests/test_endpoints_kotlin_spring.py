from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, code):
    (tmp_path / "A.kt").write_text(code)
    return sorted((e["method"], e["path"], e["unresolved"]) for e in map_api_endpoints(tmp_path)["endpoints"])


def test_controller_prefix_and_verb_annotations(tmp_path):
    code = """
@RestController
@RequestMapping("/users")
class A {
    @GetMapping("/{id}")
    fun g(@PathVariable id: Long) = 1
    @PostMapping(value = ["/new"])
    fun p() = 2
    @DeleteMapping
    fun d() = 3
}
"""
    assert _routes(tmp_path, code) == [
        ("DELETE", "/users", False), ("GET", "/users/{id}", False), ("POST", "/users/new", False),
    ]


def test_request_mapping_method_and_several_paths(tmp_path):
    code = """
@Controller
class A {
    @RequestMapping(path = ["/a", "/b"], method = [RequestMethod.PUT])
    fun q() = 1
    @RequestMapping("/any")
    fun r() = 2
}
"""
    assert _routes(tmp_path, code) == [
        ("ANY", "/any", False), ("PUT", "/a", False), ("PUT", "/b", False),
    ]


def test_constant_path_is_reported_unresolved(tmp_path):
    code = "@RestController\nclass A {\n    @GetMapping(Api.USERS)\n    fun g() = 1\n}\n"
    assert _routes(tmp_path, code) == [("GET", "/", True)]


def test_class_without_a_controller_annotation_is_ignored(tmp_path):
    code = "class A {\n    @GetMapping(\"/x\")\n    fun g() = 1\n}\n"
    assert _routes(tmp_path, code) == []


def test_property_placeholder_with_a_default_is_replaced_by_it(tmp_path):
    code = '@RestController\nclass A {\n    @GetMapping("\\${api.base-path:/api}/users")\n    fun g() = 1\n}\n'
    (tmp_path / "A.kt").write_text(code)
    got = [(e["path"], e["unresolved"], bool(e["note"])) for e in map_api_endpoints(tmp_path)["endpoints"]]
    assert got == [("/api/users", False, True)]


def test_property_placeholder_without_a_default_is_unresolved(tmp_path):
    code = '@RestController\nclass A {\n    @GetMapping("\\${api.base-path}/users")\n    fun g() = 1\n}\n'
    (tmp_path / "A.kt").write_text(code)
    got = [(e["unresolved"]) for e in map_api_endpoints(tmp_path)["endpoints"]]
    assert got == [True]


def test_fully_qualified_annotation_names_are_recognised(tmp_path):
    code = """@org.springframework.web.bind.annotation.RestController
class A {
    @org.springframework.web.bind.annotation.GetMapping("/x")
    fun g() = 1
}
"""
    assert _routes(tmp_path, code) == [("GET", "/x", False)]


def test_constant_controller_prefix_marks_routes_unresolved(tmp_path):
    code = '@RestController\n@RequestMapping(ApiPaths.BASE)\nclass A {\n    @GetMapping("/x")\n    fun g() = 1\n}\n'
    (tmp_path / "A.kt").write_text(code)
    got = [(e["path"], e["unresolved"], bool(e["note"])) for e in map_api_endpoints(tmp_path)["endpoints"]]
    assert got == [("/x", True, True)]


def test_other_string_attributes_are_not_paths(tmp_path):
    code = """@RestController
class A {
    @RequestMapping(name = "myBean", path = ["/real"], params = "a,b", method = [RequestMethod.POST])
    fun g() = 1
    @GetMapping(value = "/v", produces = "text/plain")
    fun h() = 2
}
"""
    assert _routes(tmp_path, code) == [("GET", "/v", False), ("POST", "/real", False)]
