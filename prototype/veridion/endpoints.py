from tree_sitter import Node

_ROUTE_VERB_METHODS = {"get", "post", "put", "delete", "patch"}


def _string_literal_text(node: Node, source: bytes) -> str:
    raw = source[node.start_byte : node.end_byte].decode()
    if raw.startswith(("r'", 'r"', "R'", 'R"')):
        raw = raw[1:]
    return raw.strip("'\"")


def _extract_flask_fastapi_routes(root: Node, source: bytes, rel_path: str) -> list[dict]:
    entries: list[dict] = []

    def walk(n: Node) -> None:
        if n.type == "decorated_definition":
            definition = n.child_by_field_name("definition")
            handler = "unknown"
            if definition is not None and definition.type == "function_definition":
                name_node = definition.child_by_field_name("name")
                if name_node is not None:
                    handler = source[name_node.start_byte : name_node.end_byte].decode()

            for decorator in (c for c in n.children if c.type == "decorator"):
                call = next((c for c in decorator.named_children if c.type == "call"), None)
                if call is None:
                    continue
                func = call.child_by_field_name("function")
                if func is None or func.type != "attribute":
                    continue
                attribute_node = func.child_by_field_name("attribute")
                if attribute_node is None:
                    continue
                attribute_name = source[
                    attribute_node.start_byte : attribute_node.end_byte
                ].decode()

                args = call.child_by_field_name("arguments")
                if args is None:
                    continue
                path_node = next((a for a in args.named_children if a.type == "string"), None)
                if path_node is None:
                    continue
                path = _string_literal_text(path_node, source)
                line = decorator.start_point[0] + 1

                if attribute_name == "route":
                    methods = ["GET"]
                    for arg in args.named_children:
                        if arg.type != "keyword_argument":
                            continue
                        kw_name = arg.child_by_field_name("name")
                        if kw_name is None:
                            continue
                        if source[kw_name.start_byte : kw_name.end_byte].decode() != "methods":
                            continue
                        value = arg.child_by_field_name("value")
                        if value is not None and value.type == "list":
                            methods = [
                                _string_literal_text(item, source).upper()
                                for item in value.named_children
                                if item.type == "string"
                            ]
                    for method in methods:
                        entries.append(
                            {
                                "method": method,
                                "path": path,
                                "framework": "flask",
                                "file": rel_path,
                                "line": line,
                                "handler": handler,
                                "unresolved": False,
                            }
                        )
                elif attribute_name in _ROUTE_VERB_METHODS:
                    entries.append(
                        {
                            "method": attribute_name.upper(),
                            "path": path,
                            "framework": "flask_or_fastapi",
                            "file": rel_path,
                            "line": line,
                            "handler": handler,
                            "unresolved": False,
                        }
                    )
        for child in n.children:
            walk(child)

    walk(root)
    return entries
