"""Express router mounts resolved across files, and `route()` chains.

`router.get('/:id', h)` in routes/users.js is reachable at the prefix the router is mounted under, written elsewhere:
`app.use('/users', usersRouter)`. This follows a mount through
  * CommonJS (`require('./x')`, destructured `const { a, b: c } = require('./x')`, `require('./x').name`,
    `module.exports = router | { a, b } | function factory`, `exports.name = router`) and ES modules
    (`import x`, `import { a as b }`, `export default`, `export const router = Router()`, `export { a }`),
  * a router built and returned by a factory function (`app.use('/shop', makeShop())`),
  * nesting (`parent.use('/reports', reports)`; prefixes compose) and a router mounted at several prefixes.
A mount whose prefix is not a string literal marks the routes behind it `unresolved`; a mount whose router cannot be
found in the repository (a package, a computed value) stays reported as the unresolved `use` it always was.
"""

from __future__ import annotations

from typing import Callable

from tree_sitter import Node

UNKNOWN = "\0unknown"
_VERBS = ("get", "post", "put", "delete", "patch", "all")
_ROUTER_FACTORY_NAMES = ("Router", "express.Router")


def _text(source: bytes, node: Node | None) -> str:
    return source[node.start_byte:node.end_byte].decode(errors="ignore") if node is not None else ""


def _string(source: bytes, node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "string":
        return _text(source, node)[1:-1]
    if node.type == "template_string" and not any(c.type == "template_substitution" for c in node.children):
        return _text(source, node)[1:-1]
    return None


def _walk(root: Node):
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def join_path(prefix: str, path: str) -> str:
    if not prefix:
        return path
    joined = prefix.rstrip("/") + "/" + path.lstrip("/")
    return joined if joined.startswith("/") else "/" + joined


_CHAINABLE = frozenset({"use", "get", "post", "put", "delete", "patch", "all", "param"})


def _unwrap_chain(source: bytes, value: Node | None) -> Node | None:
    """`Router().use(a).use(b)` -> the `Router()` call at its base (a router's own methods return the router)."""
    while value is not None and value.type == "call_expression":
        function = value.child_by_field_name("function")
        if function is not None and function.type == "member_expression" and _text(
            source, function.child_by_field_name("property")
        ) in _CHAINABLE:
            value = function.child_by_field_name("object")
        else:
            break
    return value


def _router_kind(source: bytes, value: Node | None) -> str | None:
    value = _unwrap_chain(source, value)
    if value is None or value.type != "call_expression":
        return None
    function_node = value.child_by_field_name("function")
    function = _text(source, function_node)
    if function == "express" or (
        function_node is not None and function_node.type == "call_expression"
        and _text(source, function_node.child_by_field_name("function")) == "require"
        and "express" in _text(source, function_node.child_by_field_name("arguments"))
    ):  # express() and require('express')()
        return "app"
    if function in _ROUTER_FACTORY_NAMES or function.endswith(".Router"):
        return "router"
    return None


def _returned_router(source: bytes, function_node: Node, routers: set[str]) -> str | None:
    local = set()
    for n in _walk(function_node):
        if n.type == "variable_declarator":
            name = n.child_by_field_name("name")
            if name is not None and name.type == "identifier" and _router_kind(source, n.child_by_field_name("value")):
                local.add(_text(source, name))
    for n in _walk(function_node):
        if n.type == "return_statement":
            value = next((c for c in n.named_children), None)
            if value is not None and value.type == "identifier" and _text(source, value) in (local | routers):
                return _text(source, value)
    return None


def _chain_owner(source: bytes, call: Node) -> str | None:
    """The name a fluent chain's router goes by: the variable it initialises, or `<default>` when the chain is
    exported directly (`export default Router().use(...)`, `module.exports = Router().use(...)`)."""
    top = call
    while (
        top.parent is not None and top.parent.type == "member_expression"
        and top.parent.parent is not None and top.parent.parent.type == "call_expression"
        and top.parent.child_by_field_name("object") == top
    ):
        top = top.parent.parent
    parent = top.parent
    if parent is None:
        return None
    if parent.type == "variable_declarator":
        name = parent.child_by_field_name("name")
        return _text(source, name) if name is not None and name.type == "identifier" else None
    if parent.type == "export_statement" or (
        parent.type == "assignment_expression" and _text(source, parent.child_by_field_name("left")) == "module.exports"
    ):
        return "<default>"
    return None


def collect_file_facts(root: Node, source: bytes, rel_path: str, resolve_module: Callable[[str], str | None]) -> dict:
    routers: dict[str, str] = {}
    functions: dict[str, str] = {}
    exports: dict[str, tuple[str, str]] = {}
    imports: dict[str, tuple[str, str]] = {}
    mounts: list[dict] = []

    for n in _walk(root):
        if n.type == "variable_declarator":
            name, value = n.child_by_field_name("name"), n.child_by_field_name("value")
            if name is not None and name.type == "identifier":
                kind = _router_kind(source, value)
                if kind:
                    routers[_text(source, name)] = kind
    for n in _walk(root):
        # function name -> the router it returns
        if n.type == "function_declaration":
            name = n.child_by_field_name("name")
            var = _returned_router(source, n, set(routers))
            if name is not None and var:
                functions[_text(source, name)] = var
        elif n.type == "variable_declarator":
            name, value = n.child_by_field_name("name"), n.child_by_field_name("value")
            if name is not None and name.type == "identifier" and value is not None and value.type in ("arrow_function", "function_expression", "function"):
                var = _returned_router(source, value, set(routers))
                if var:
                    functions[_text(source, name)] = var

    def local_target(node: Node | None) -> tuple[str, str] | None:
        if node is None:
            return None
        if node.type == "identifier":
            name = _text(source, node)
            if name in routers:
                return ("router", name)
            if name in functions:
                return ("factory", name)
        if node.type == "call_expression" and _router_kind(source, node):
            routers.setdefault("<default>", "router")  # an unnamed router exported directly
            return ("router", "<default>")
        if node.type in ("function_expression", "function", "arrow_function", "function_declaration"):
            var = _returned_router(source, node, set(routers))
            if var:
                nm = node.child_by_field_name("name")
                key = _text(source, nm) if nm is not None else "<anonymous>"
                functions[key] = var
                return ("factory", key)
        return None

    for n in _walk(root):
        # ---- exports
        if n.type == "assignment_expression":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            left_text = _text(source, left)
            if left_text == "module.exports":
                if right is not None and right.type == "object":
                    for p in right.named_children:
                        if p.type == "shorthand_property_identifier":
                            t = local_target(p)
                            if t is None:
                                nm = _text(source, p)
                                t = ("router", nm) if nm in routers else ("factory", nm) if nm in functions else None
                            if t:
                                exports[_text(source, p)] = t
                        elif p.type == "pair":
                            key, val = p.child_by_field_name("key"), p.child_by_field_name("value")
                            t = local_target(val)
                            if t and key is not None:
                                exports[_text(source, key)] = t
                else:
                    t = local_target(right)
                    if t:
                        exports["default"] = t
            elif left_text.startswith(("exports.", "module.exports.")):
                t = local_target(right)
                if t:
                    exports[left_text.rsplit(".", 1)[1]] = t
        elif n.type == "export_statement":
            default = any(c.type == "default" for c in n.children)
            declaration = n.child_by_field_name("declaration")
            value = n.child_by_field_name("value")
            if default:
                t = local_target(value or declaration)
                if t:
                    exports["default"] = t
            if declaration is not None:
                if declaration.type == "function_declaration":
                    nm = declaration.child_by_field_name("name")
                    t = local_target(declaration)
                    if t and nm is not None and not default:
                        exports[_text(source, nm)] = t
                elif declaration.type == "lexical_declaration":
                    for d in declaration.named_children:
                        if d.type == "variable_declarator":
                            nm = d.child_by_field_name("name")
                            if nm is not None and nm.type == "identifier":
                                t = local_target(nm)
                                if t:
                                    exports[_text(source, nm)] = t
            for c in n.children:
                if c.type == "export_clause":
                    for spec in c.named_children:
                        if spec.type == "export_specifier":
                            nm, alias = spec.child_by_field_name("name"), spec.child_by_field_name("alias")
                            t = local_target(nm)
                            if t:
                                exports[_text(source, alias or nm)] = t
        # ---- imports
        elif n.type == "variable_declarator":
            name, value = n.child_by_field_name("name"), n.child_by_field_name("value")
            required = value
            member = None
            if value is not None and value.type == "member_expression":
                required, member = value.child_by_field_name("object"), _text(source, value.child_by_field_name("property"))
            if required is not None and required.type == "call_expression" and _text(source, required.child_by_field_name("function")) == "require":
                arguments = required.child_by_field_name("arguments")
                spec = _string(source, arguments.named_children[0]) if arguments is not None and arguments.named_children else None
                file = resolve_module(spec) if spec else None
                if file and name is not None:
                    if name.type == "identifier":
                        imports[_text(source, name)] = (file, member or "default")
                    elif name.type == "object_pattern":
                        for p in name.named_children:
                            if p.type == "shorthand_property_identifier_pattern":
                                imports[_text(source, p)] = (file, _text(source, p))
                            elif p.type == "pair_pattern":
                                key, val = p.child_by_field_name("key"), p.child_by_field_name("value")
                                if val is not None and val.type == "identifier":
                                    imports[_text(source, val)] = (file, _text(source, key))
        elif n.type == "import_statement":
            spec = _string(source, n.child_by_field_name("source"))
            file = resolve_module(spec) if spec else None
            if not file:
                continue
            for c in n.children:
                if c.type != "import_clause":
                    continue
                for part in c.children:
                    if part.type == "identifier":
                        imports[_text(source, part)] = (file, "default")
                    elif part.type == "namespace_import":
                        ident = next((x for x in part.children if x.type == "identifier"), None)
                        if ident is not None:
                            imports[_text(source, ident)] = (file, "*")
                    elif part.type == "named_imports":
                        for spec_node in part.named_children:
                            nm, alias = spec_node.child_by_field_name("name"), spec_node.child_by_field_name("alias")
                            imports[_text(source, alias or nm)] = (file, _text(source, nm))

    def ref(node: Node | None):
        """A mount argument as ('node', local) / ('import', file, export) / ('factory', local) /
        ('factory-import', file, export), or None when it is not something a router can come from."""
        if node is None:
            return None
        if node.type == "identifier":
            name = _text(source, node)
            if name in routers:
                return ("node", name)
            if name in imports:
                return ("import", *imports[name])
            return None
        if node.type == "member_expression":
            obj, prop = node.child_by_field_name("object"), _text(source, node.child_by_field_name("property"))
            if obj is not None and obj.type == "identifier" and _text(source, obj) in imports:
                return ("import", imports[_text(source, obj)][0], prop)
            return None
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            if function is not None and function.type == "identifier":
                name = _text(source, function)
                if name == "require":
                    arguments = node.child_by_field_name("arguments")
                    spec = _string(source, arguments.named_children[0]) if arguments is not None and arguments.named_children else None
                    file = resolve_module(spec) if spec else None
                    return ("import", file, "default") if file else None
                if name in functions:
                    return ("factory", name)
                if name in imports:
                    return ("factory-import", *imports[name])
            if function is not None and function.type == "member_expression":
                obj, prop = function.child_by_field_name("object"), _text(source, function.child_by_field_name("property"))
                if obj is not None and obj.type == "identifier" and _text(source, obj) in imports:
                    return ("factory-import", imports[_text(source, obj)][0], prop)
        return None

    for n in _walk(root):
        if n.type != "call_expression":
            continue
        function = n.child_by_field_name("function")
        if function is None or function.type != "member_expression" or _text(source, function.child_by_field_name("property")) != "use":
            continue
        receiver = function.child_by_field_name("object")
        if receiver is None:
            continue
        if receiver.type == "identifier":
            receiver_name = _text(source, receiver)
        elif receiver.type == "call_expression" and _router_kind(source, receiver):
            receiver_name = _chain_owner(source, n)
            if receiver_name is None:
                continue
        else:
            continue
        arguments = n.child_by_field_name("arguments")
        args = arguments.named_children if arguments is not None else []
        if not args:
            continue
        target_index, target = next(((i, r) for i, a in enumerate(args) for r in [ref(a)] if r is not None), (None, None))
        if target is None:
            continue
        prefix: str | None = ""
        if target_index and target_index >= 1:
            literal = _string(source, args[0])
            prefix = literal if literal is not None else UNKNOWN
        mounts.append({"recv": receiver_name, "target": target, "prefix": prefix, "site": (rel_path, n.start_byte)})
    return {
        "file": rel_path, "routers": routers, "functions": functions, "exports": exports, "imports": imports, "mounts": mounts,
    }


def resolve(all_facts: list[dict]):
    """(router node (file, local name) -> every prefix it is reachable at, nodes behind an unknown prefix,
    mount call sites that resolved to a router, the factory/export tables used to resolve names in a file)."""
    by_file = {f["file"]: f for f in all_facts}

    def node_of(file: str, target) -> tuple[str, str] | None:
        kind = target[0]
        facts = by_file.get(file)
        if kind == "node":
            return (file, target[1]) if facts and target[1] in facts["routers"] else None
        if kind == "factory":
            var = facts["functions"].get(target[1]) if facts else None
            return (file, var) if var else None
        other = by_file.get(target[1])
        if other is None:
            return None
        exported = other["exports"].get(target[2])
        if kind == "import" and exported and exported[0] == "router":
            return (target[1], exported[1])
        if kind == "factory-import" and exported and exported[0] == "factory":
            var = other["functions"].get(exported[1])
            return (target[1], var) if var else None
        return None

    def receiver_node(file: str, name: str) -> tuple[str, str] | None:
        facts = by_file.get(file)
        if facts is None:
            return None  # a file the pre-filter did not collect facts for (no express / .use( / Router in it)
        if name in facts["routers"]:
            return (file, name)
        if name in facts["imports"]:
            return node_of(file, ("import", *facts["imports"][name]))
        return None

    edges: list[tuple[tuple[str, str], tuple[str, str], str | None]] = []
    resolved_sites: set[tuple[str, int]] = set()
    for facts in all_facts:
        for m in facts["mounts"]:
            target = node_of(facts["file"], m["target"])
            recv = receiver_node(facts["file"], m["recv"])
            if target is None or recv is None:
                continue
            edges.append((recv, target, m["prefix"]))
            resolved_sites.add(m["site"])
    by_target: dict[tuple[str, str], list] = {}
    for recv, target, prefix in edges:
        by_target.setdefault(target, []).append((recv, prefix))
    memo: dict[tuple[str, str], list[str]] = {}
    unresolved: set[tuple[str, str]] = set()
    cuts = [0]  # how many times a mount cycle was cut short

    def eff(node, stack=()):
        if node in memo:
            return memo[node]
        if node in stack:
            cuts[0] += 1
            return [""]
        cuts_before = cuts[0]
        parents = by_target.get(node, [])
        if not parents:
            memo[node] = [""]
            return memo[node]
        out: list[str] = []
        for recv, prefix in parents:
            base = eff(recv, stack + (node,))
            if prefix == UNKNOWN or recv in unresolved:
                unresolved.add(node)
            for b in base:
                full = join_path(b, prefix if prefix and prefix != UNKNOWN else "") if (prefix and prefix != UNKNOWN) else b
                if full not in out:
                    out.append(full)
        if cuts[0] == cuts_before:
            memo[node] = out  # a result computed while a cycle was cut short depends on where the walk started: not cached
        return out

    final: dict[tuple[str, str], list[str]] = {}
    for node in sorted({t for _r, t, _p in edges}):
        final[node] = memo[node] if node in memo else eff(node)
    return final, unresolved, resolved_sites, receiver_node


def extract_routes(
    root: Node, source: bytes, rel_path: str, prefixes: dict, unresolved_nodes: set, resolved_sites: set,
    receiver_node, helpers,
) -> list[dict]:
    """Route entries of one file with mounted prefixes applied, `route()` chains expanded and resolved `use` mounts
    left out. `helpers` = (looks_like_express_path, express_handler_label, js_string_literal_text)."""
    looks_like_path, handler_label, string_text = helpers
    entries: list[dict] = []

    def emit(method, path, handler_node, line, receiver_name):
        node = receiver_node(rel_path, receiver_name) if receiver_name else None
        bases = prefixes.get(node, [""]) if node is not None else [""]
        is_unresolved = node in unresolved_nodes if node is not None else False
        for base in bases:
            entries.append({
                "method": method, "path": join_path(base, path) if base else path, "framework": "express",
                "file": rel_path, "line": line, "handler": handler_label(handler_node, source),
                "unresolved": is_unresolved,
                "note": "router is mounted under a prefix that is not a string literal" if is_unresolved else None,
            })

    for n in _walk(root):
        if n.type != "call_expression":
            continue
        func = n.child_by_field_name("function")
        if func is None or func.type != "member_expression":
            continue
        prop = func.child_by_field_name("property")
        args = n.child_by_field_name("arguments")
        if prop is None or args is None:
            continue
        method_name = _text(source, prop)
        named = args.named_children
        line = n.start_point[0] + 1
        obj = func.child_by_field_name("object")
        receiver_name = _text(source, obj) if obj is not None and obj.type == "identifier" else None
        if method_name in _VERBS and obj is not None and obj.type == "call_expression":
            # R.route('/p').get(h).post(h): walk down the chain to the route(...) call
            verbs = [method_name]
            handlers = [named[0] if named else None]
            cursor = obj
            route_path = None
            root_name = None
            while cursor is not None and cursor.type == "call_expression":
                f = cursor.child_by_field_name("function")
                if f is None or f.type != "member_expression":
                    break
                p = _text(source, f.child_by_field_name("property"))
                a = cursor.child_by_field_name("arguments")
                if p == "route":
                    first = a.named_children[0] if a is not None and a.named_children else None
                    route_path = string_text(first, source) if first is not None and first.type == "string" else None
                    o = f.child_by_field_name("object")
                    root_name = _text(source, o) if o is not None and o.type == "identifier" else None
                    break
                if p in _VERBS:
                    verbs.append(p)
                    handlers.append(a.named_children[0] if a is not None and a.named_children else None)
                    cursor = f.child_by_field_name("object")
                else:
                    break
            if route_path is not None and root_name is not None:
                # the chain's outermost call is visited first; inner verbs are visited when the walk reaches them,
                # so only emit from the outermost call of a chain
                parent = n.parent
                if parent is not None and parent.type == "member_expression" and parent.parent is not None and parent.parent.type == "call_expression":
                    continue
                for verb, handler in zip(reversed(verbs), reversed(handlers)):
                    emit("ANY" if verb == "all" else verb.upper(), route_path, handler, line, root_name)
                continue
            if cursor is not None and cursor.type == "identifier":
                # `router.get('/a', hA).post('/b', hB)`: a chain of verb calls rooted at a router; the outer verb is an
                # ordinary route on that router (the inner ones are visited when the walk reaches them)
                receiver_name = _text(source, cursor)
            else:
                continue
        if method_name == "route" and named and named[0].type == "string":
            # the chain's first link: handled from the outermost verb call above
            continue
        if named and named[0].type == "string":
            path = string_text(named[0], source)
            if not looks_like_path(path):
                continue
            handler_node = named[1] if len(named) > 1 else None
            if method_name in _VERBS:
                emit("ANY" if method_name == "all" else method_name.upper(), path, handler_node, line, receiver_name)
            elif method_name == "use":
                if (rel_path, n.start_byte) in resolved_sites:
                    continue
                entries.append({
                    "method": None, "path": path, "framework": "express", "file": rel_path, "line": line,
                    "handler": "app.use(...)", "unresolved": True, "note": None,
                })
        elif method_name == "use" and (rel_path, n.start_byte) in resolved_sites:
            continue
    return entries
