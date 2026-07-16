from tree_sitter import Parser

from veridion.endpoints import _extract_flask_fastapi_routes
from veridion.scanner.graph import PY_LANGUAGE


def parse_python(source: str):
    parser = Parser()
    parser.language = PY_LANGUAGE
    tree = parser.parse(source.encode())
    return tree.root_node, source.encode()


def test_extract_flask_route_decorator_with_methods():
    root, source = parse_python(
        '@app.route("/users/<int:id>", methods=["GET", "POST"])\n'
        "def get_user(id):\n"
        "    pass\n"
    )

    entries = _extract_flask_fastapi_routes(root, source, "app/routes.py")

    assert len(entries) == 2
    methods = {e["method"] for e in entries}
    assert methods == {"GET", "POST"}
    for entry in entries:
        assert entry["path"] == "/users/<int:id>"
        assert entry["framework"] == "flask"
        assert entry["file"] == "app/routes.py"
        assert entry["handler"] == "get_user"
        assert entry["unresolved"] is False


def test_extract_flask_route_defaults_to_get_when_no_methods_kwarg():
    root, source = parse_python('@app.route("/ping")\ndef ping():\n    pass\n')

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert len(entries) == 1
    assert entries[0]["method"] == "GET"


def test_extract_fastapi_verb_decorator_labeled_ambiguous():
    root, source = parse_python(
        '@router.get("/items/{item_id}")\ndef read_item(item_id):\n    pass\n'
    )

    entries = _extract_flask_fastapi_routes(root, source, "app/api.py")

    assert entries == [
        {
            "method": "GET",
            "path": "/items/{item_id}",
            "framework": "flask_or_fastapi",
            "file": "app/api.py",
            "line": 1,
            "handler": "read_item",
            "unresolved": False,
        }
    ]


def test_extract_flask_fastapi_ignores_non_route_decorators():
    root, source = parse_python("@staticmethod\ndef helper():\n    pass\n")

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert entries == []


def test_extract_flask_fastapi_handles_multiple_decorators_on_one_function():
    root, source = parse_python(
        '@app.get("/a")\n@some_other_decorator\ndef handler():\n    pass\n'
    )

    entries = _extract_flask_fastapi_routes(root, source, "app.py")

    assert len(entries) == 1
    assert entries[0]["path"] == "/a"
