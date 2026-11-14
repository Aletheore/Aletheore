"""Flask blueprint URL prefixes and `add_url_rule`, resolved across files.

A route decorated on a blueprint (`@bp.route("/x")`) is reachable at the prefix the blueprint is registered under,
which is written elsewhere: `app.register_blueprint(bp, url_prefix="/v1")`, a parent blueprint, or the blueprint's own
`url_prefix=` argument. Flask's rules, applied here:
  * a prefix given when registering REPLACES the blueprint's own constructor prefix (it does not add to it);
  * a blueprint registered on another blueprint is reachable at the parent's full prefix followed by its own;
  * a blueprint registered twice is reachable at each prefix;
  * a blueprint nobody registers keeps its constructor prefix.
A prefix that is not a string literal cannot be known statically: the routes it governs are reported flagged
`unresolved`, never as a bare path.
"""

from __future__ import annotations

from typing import Callable

from tree_sitter import Node

UNKNOWN = "\0unknown"
_VERBS = ("get", "post", "put", "delete", "patch", "head", "options")


def _text(source: bytes, node: Node) -> str:
    return source[node.start_byte:node.end_byte].decode(errors="ignore")


def _literal(source: bytes, node: Node | None) -> str | None:
    if node is None or node.type != "string":
        return None
    raw = _text(source, node)
    prefix = ""
    while raw[:1] and raw[:1] not in "'\"":
        prefix += raw[:1]
        raw = raw[1:]
    if "f" in prefix.lower():
        return None  # an f-string is not a literal
    return raw.strip("'\"")


def _walk(root: Node):
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _kwarg(source: bytes, args: Node, name: str) -> Node | None:
    for a in args.named_children:
        if a.type == "keyword_argument":
            key = a.child_by_field_name("name")
            if key is not None and _text(source, key) == name:
                return a.child_by_field_name("value")
    return None


def _prefix_value(source: bytes, value: Node | None) -> str | None:
    """None when absent, UNKNOWN when present but not a string literal, else the literal."""
    if value is None or value.type == "none":
        return None
    literal = _literal(source, value)
    return literal if literal is not None else UNKNOWN


def join_prefix(*parts: str) -> str:
    cleaned = [p.strip("/") for p in parts if p and p != UNKNOWN and p.strip("/")]
    return "/" + "/".join(cleaned) if cleaned else ""


def collect_file_facts(root: Node, source: bytes, rel_path: str, resolve_import: Callable[[str], tuple[str, str] | None]) -> dict:
    """Blueprint definitions, registrations and blueprint factories of one file.

    `resolve_import(name)` gives (the repo file, the name it has there) a `from x import name [as alias]` binding
    points at, or None."""
    defs: dict[str, str | None] = {}
    factories: dict[str, str] = {}
    module_functions: set[str] = set()
    for n in _walk(root):
        if n.type == "assignment":
            right = n.child_by_field_name("right")
            left = n.child_by_field_name("left")
            if right is not None and right.type == "call" and left is not None and left.type == "identifier":
                function = right.child_by_field_name("function")
                args = right.child_by_field_name("arguments")
                if function is not None and args is not None and _text(source, function).split(".")[-1] == "Blueprint":
                    defs[_text(source, left)] = _prefix_value(source, _kwarg(source, args, "url_prefix"))
    for child in root.children:
        if child.type != "function_definition":
            continue
        name_node = child.child_by_field_name("name")
        if name_node is None:
            continue
        module_functions.add(_text(source, name_node))
        local = {
            _text(source, a.child_by_field_name("left")) for a in _walk(child)
            if a.type == "assignment" and a.child_by_field_name("left") is not None
            and a.child_by_field_name("right") is not None and a.child_by_field_name("right").type == "call"
            and a.child_by_field_name("right").child_by_field_name("function") is not None
            and _text(source, a.child_by_field_name("right").child_by_field_name("function")).split(".")[-1] == "Blueprint"
        }
        for r in _walk(child):
            if r.type == "return_statement":
                value = next((c for c in r.named_children), None)
                if value is not None and value.type == "identifier" and _text(source, value) in local:
                    factories[_text(source, name_node)] = _text(source, value)

    def ref(node: Node | None):
        if node is None:
            return None
        if node.type == "identifier":
            name = _text(source, node)
            if name in defs:
                return (rel_path, name)
            bound = resolve_import(name)
            return bound if bound else None
        if node.type == "attribute":
            obj, attr = node.child_by_field_name("object"), node.child_by_field_name("attribute")
            if obj is not None and obj.type == "identifier" and attr is not None:
                bound = resolve_import(_text(source, obj))
                return (bound[0], _text(source, attr)) if bound else None
            return None
        if node.type == "call":
            function = node.child_by_field_name("function")
            if function is not None and function.type == "identifier":
                name = _text(source, function)
                if name in module_functions:
                    return ("factory", rel_path, name)
                bound = resolve_import(name)
                return ("factory", bound[0], bound[1]) if bound else None
        return None

    regs: list[dict] = []
    for n in _walk(root):
        if n.type != "call":
            continue
        function = n.child_by_field_name("function")
        args = n.child_by_field_name("arguments")
        if function is None or args is None or function.type != "attribute":
            continue
        attr = function.child_by_field_name("attribute")
        if attr is None or _text(source, attr) != "register_blueprint":
            continue
        positional = [a for a in args.named_children if a.type != "keyword_argument"]
        if not positional:
            continue
        target = ref(positional[0])
        if target is None:
            continue
        receiver = ref(function.child_by_field_name("object")) if function.child_by_field_name("object") is not None and function.child_by_field_name("object").type == "identifier" else None
        regs.append({"recv": receiver, "target": target, "prefix": _prefix_value(source, _kwarg(source, args, "url_prefix"))})
    return {"defs": {(rel_path, k): v for k, v in defs.items()},
            "factories": {(rel_path, k): v for k, v in factories.items()}, "regs": regs}


def effective_prefixes(all_facts: list[dict]) -> tuple[dict[tuple[str, str], list[str]], set[tuple[str, str]]]:
    """(blueprint key -> every full prefix it is reachable at, keys whose prefix could not be determined)."""
    defs: dict[tuple[str, str], str | None] = {}
    factories: dict[tuple[str, str], str] = {}
    regs: list[dict] = []
    for facts in all_facts:
        defs.update(facts["defs"])
        factories.update(facts["factories"])
        regs.extend(facts["regs"])
    by_target: dict[tuple[str, str], list[dict]] = {}
    for reg in regs:
        target = reg["target"]
        if len(target) == 3:  # ("factory", file, function): the blueprint the function returns
            var = factories.get((target[1], target[2]))
            target = (target[1], var) if var else None
        if target is not None and target in defs:
            by_target.setdefault(target, []).append(reg)
    memo: dict[tuple[str, str], list[str]] = {}
    unresolved: set[tuple[str, str]] = set()
    cuts = [0]  # how many times a registration cycle was cut short

    def eff(key: tuple[str, str], stack: tuple = ()) -> list[str]:
        if key in memo:
            return memo[key]
        if key in stack:
            cuts[0] += 1
            return [""]
        cuts_before = cuts[0]
        ctor = defs.get(key)
        registrations = by_target.get(key, [])
        out: list[str] = []
        if not registrations:
            if ctor == UNKNOWN:
                unresolved.add(key)
            out = [join_prefix(ctor or "")]
        for reg in registrations:
            own = reg["prefix"] if reg["prefix"] is not None else ctor
            if own == UNKNOWN:
                unresolved.add(key)
            base = eff(reg["recv"], stack + (key,)) if reg["recv"] in defs else [""]
            if reg["recv"] in defs and reg["recv"] in unresolved:
                unresolved.add(key)
            for b in base:
                full = join_prefix(b, own or "")
                if full not in out:
                    out.append(full)
        if cuts[0] == cuts_before:
            memo[key] = out  # a result computed while a cycle was cut short depends on where the walk started: not cached
        return out

    final: dict[tuple[str, str], list[str]] = {}
    for key in defs:
        # each blueprint's answer comes from a walk rooted at that blueprint; only results that did not involve a cut
        # cycle are shared between walks, so the answer does not depend on the order the blueprints are visited
        final[key] = memo[key] if key in memo else eff(key)
    return final, unresolved


def extract_add_url_rules(
    root: Node, source: bytes, rel_path: str, prefixes: dict[tuple[str, str], list[str]],
    unresolved_keys: set[tuple[str, str]],
    key_for=None,
) -> list[dict]:
    """Routes registered with `app.add_url_rule(...)` / `bp.add_url_rule(...)`."""
    classes: dict[str, list[str]] = {}
    functions: set[str] = set()
    as_view_vars: dict[str, str] = {}
    for n in _walk(root):
        if n.type == "class_definition":
            name = n.child_by_field_name("name")
            body = n.child_by_field_name("body")
            if name is not None and body is not None:
                classes[_text(source, name)] = [
                    _text(source, c.child_by_field_name("name")).upper() for c in body.named_children
                    if c.type == "function_definition" and _text(source, c.child_by_field_name("name")) in _VERBS
                ]
        elif n.type == "function_definition":
            name = n.child_by_field_name("name")
            if name is not None:
                functions.add(_text(source, name))
        elif n.type == "assignment":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if left is not None and left.type == "identifier" and right is not None and right.type == "call":
                fn = right.child_by_field_name("function")
                if fn is not None and fn.type == "attribute" and _text(source, fn.child_by_field_name("attribute")) == "as_view":
                    obj = fn.child_by_field_name("object")
                    if obj is not None and obj.type == "identifier":
                        as_view_vars[_text(source, left)] = _text(source, obj)

    def view_class(node: Node | None) -> str | None:
        if node is None:
            return None
        if node.type == "call":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type == "attribute" and _text(source, fn.child_by_field_name("attribute")) == "as_view":
                obj = fn.child_by_field_name("object")
                return _text(source, obj) if obj is not None and obj.type == "identifier" else None
        if node.type == "identifier":
            return as_view_vars.get(_text(source, node))
        return None

    entries: list[dict] = []
    for n in _walk(root):
        if n.type != "call":
            continue
        function = n.child_by_field_name("function")
        args = n.child_by_field_name("arguments")
        if function is None or args is None or function.type != "attribute":
            continue
        attr = function.child_by_field_name("attribute")
        if attr is None or _text(source, attr) != "add_url_rule":
            continue
        positional = [a for a in args.named_children if a.type != "keyword_argument"]
        rule_node = _kwarg(source, args, "rule") or (positional[0] if positional else None)
        rule = _literal(source, rule_node)
        if rule is None:
            continue
        view = _kwarg(source, args, "view_func")
        if view is None:
            # add_url_rule(rule, endpoint, view_func): the view is the third positional, or the second when it is
            # not a string (endpoint omitted)
            rest = positional[1:]
            if len(rest) >= 2:
                view = rest[1]
            elif rest and rest[0].type != "string":
                view = rest[0]
        handler = "unknown"
        if view is not None:
            if view.type == "identifier":
                handler = _text(source, view)
            elif view.type == "call":
                cls = view_class(view)
                handler = cls or "unknown"
        cls = view_class(view)
        methods_node = _kwarg(source, args, "methods")
        unresolved, note = False, None
        if methods_node is not None and methods_node.type in ("list", "tuple"):
            methods = [m.upper() for m in (_literal(source, i) for i in methods_node.named_children) if m]
        elif methods_node is not None:
            methods, unresolved, note = ["GET"], True, "methods is not a literal list"
        elif cls is not None and cls in classes and classes[cls]:
            methods = classes[cls]
        elif cls is not None:
            methods, unresolved, note = ["GET"], True, f"MethodView {cls} not found in this file; methods unknown"
        else:
            methods = ["GET"]
        receiver = function.child_by_field_name("object")
        receiver_name = _text(source, receiver) if receiver is not None and receiver.type == "identifier" else None
        key = (key_for(receiver_name) if key_for else (rel_path, receiver_name)) if receiver_name else None
        bases = prefixes.get(key, [""]) if key in prefixes else [""]
        if key in unresolved_keys:
            unresolved, note = True, "blueprint prefix is not a string literal"
        line = n.start_point[0] + 1
        for base in bases:
            path = join_prefix(base, rule) if base else rule
            if rule.endswith("/") and not path.endswith("/"):
                path += "/"
            for method in methods:
                entries.append({
                    "method": method, "path": path, "framework": "flask", "file": rel_path, "line": line,
                    "handler": handler, "unresolved": unresolved, "note": note,
                })
    return entries
