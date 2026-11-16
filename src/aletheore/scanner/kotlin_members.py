"""Which file declares each Kotlin member a file uses, resolved from declared types (no compiler).

The Kotlin compiler links a file to the declaration of every symbol it resolves: `localization.usageTitle()` depends on
the file declaring `usageTitle`, although that file's type is never written there. A syntax tree can follow the same
path where the types are written down: a class table (supertypes, properties and functions with declared types), the
declared types of parameters and locals, the implicit `this` and inherited members, constructor calls, and `a.b.c`
chains. A member is resolved only when every step is certain; a name declared by two files of the same simple name, an
untyped receiver, or code inside a lambda (whose implicit receiver can differ) resolves to nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node

_CLASS_NODES = ("class_declaration", "object_declaration")


@dataclass
class KClass:
    name: str
    file: str
    supers: list[str] = field(default_factory=list)
    props: dict[str, str | None] = field(default_factory=dict)
    funs: dict[str, str | None] = field(default_factory=dict)


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode(errors="ignore")


def type_name(node: Node | None, source: bytes) -> str | None:
    """Simple name of a written type: `Foo`, `a.b.Foo`, `Foo?`, `Foo<Bar>` all give `Foo` (function types give None)."""
    while node is not None and node.type in ("nullable_type", "parenthesized_type"):
        node = next((c for c in node.children if c.type in ("user_type", "nullable_type", "parenthesized_type")), None)
    if node is None or node.type != "user_type":
        return None
    idents = [c for c in node.children if c.type in ("identifier", "simple_user_type")]
    if not idents:
        return None
    last = idents[-1]
    if last.type == "simple_user_type":
        last = next((c for c in last.children if c.type == "identifier"), None)
    return _text(last, source) if last is not None else None


def _declared_type(variable: Node, source: bytes) -> str | None:
    return type_name(next((c for c in variable.children if c.type in ("user_type", "nullable_type")), None), source)


def _supers(node: Node, source: bytes) -> list[str]:
    out: list[str] = []
    specs = next((c for c in node.children if c.type == "delegation_specifiers"), None)
    for spec in (specs.children if specs is not None else []):
        if spec.type != "delegation_specifier":
            continue
        target = next((c for c in spec.children if c.type in ("constructor_invocation", "user_type", "explicit_delegation")), None)
        if target is None:
            continue
        if target.type == "constructor_invocation":
            target = next((c for c in target.children if c.type == "user_type"), None)
        elif target.type == "explicit_delegation":
            target = next((c for c in target.children if c.type == "user_type"), None)
        name = type_name(target, source)
        if name:
            out.append(name)
    return out


def collect_classes(root: Node, source: bytes, rel: str, known: set[str] | None = None) -> list[KClass]:
    """Every class, interface and object declared in the file (any nesting), with its supertypes and members."""
    classes: list[KClass] = []

    def initializer_type(prop: Node) -> str | None:
        seen_eq = False
        for child in prop.children:
            if child.type == "=":
                seen_eq = True
            elif seen_eq and child.type == "call_expression":
                callee = child.children[0] if child.children else None
                if callee is not None and callee.type == "identifier":
                    name = _text(callee, source)
                    return name if name[:1].isupper() else None
                return None
        return None

    def read_body(cls: KClass, body: Node | None) -> None:
        for member in (body.children if body is not None else []):
            if member.type == "property_declaration":
                for variable in (c for c in member.children if c.type == "variable_declaration"):
                    ident = next((c for c in variable.children if c.type == "identifier"), None)
                    if ident is not None:
                        cls.props[_text(ident, source)] = _declared_type(variable, source) or initializer_type(member)
            elif member.type == "function_declaration":
                ident = next((c for c in member.children if c.type == "identifier"), None)
                if ident is None:
                    continue
                ret = None
                after_params = False
                for child in member.children:
                    if child.type == "function_value_parameters":
                        after_params = True
                    elif after_params and child.type in ("user_type", "nullable_type"):
                        ret = type_name(child, source)
                        break
                    elif child.type == "function_body":
                        break
                cls.funs[_text(ident, source)] = ret
            elif member.type == "companion_object":
                read_body(cls, next((c for c in member.children if c.type == "class_body"), None))

    def visit(node: Node) -> None:
        if node.type in _CLASS_NODES:
            ident = next((c for c in node.children if c.type == "identifier"), None)
            if ident is not None:
                cls = KClass(_text(ident, source), rel, _supers(node, source))
                ctor = next((c for c in node.children if c.type == "primary_constructor"), None)
                params = next((c for c in (ctor.children if ctor is not None else []) if c.type == "class_parameters"), None)
                for prm in (params.children if params is not None else []):
                    if prm.type == "class_parameter" and any(c.type in ("val", "var") for c in prm.children):
                        pid = next((c for c in prm.children if c.type == "identifier"), None)
                        if pid is not None:
                            cls.props[_text(pid, source)] = type_name(
                                next((c for c in prm.children if c.type in ("user_type", "nullable_type")), None), source
                            )
                read_body(cls, next((c for c in node.children if c.type in ("class_body", "enum_class_body")), None))
                classes.append(cls)
        for child in node.children:
            visit(child)

    visit(root)
    return classes


class MemberResolver:
    """Looks members up through the supertypes of the repository's classes."""

    def __init__(self, classes: dict[str, KClass | None]):
        self.classes = classes  # simple name -> its class, None when several files declare that name

    def class_of(self, name: str | None) -> KClass | None:
        return self.classes.get(name) if name else None

    def find(self, cls_name: str | None, member: str, kind: str) -> tuple[KClass, str | None] | None:
        """The class (cls_name or an ancestor) declaring `member` as a property (kind "prop") or function ("fun"),
        and its declared type."""
        seen: set[str] = set()
        queue = [cls_name]
        while queue:
            name = queue.pop(0)
            cls = self.class_of(name)
            if cls is None or name in seen:
                continue
            seen.add(name)
            table = cls.props if kind == "prop" else cls.funs
            if member in table:
                return cls, table[member]
            queue.extend(cls.supers)
        return None


def member_files(root: Node, source: bytes, rel: str, own: dict[str, KClass], resolver: MemberResolver) -> set[str]:
    """Files declaring the members this file resolves (never the file itself)."""
    found: set[str] = set()

    def note(owner: KClass | None) -> None:
        if owner is not None and owner.file != rel:
            found.add(owner.file)

    def infer(node: Node | None, env: dict, cls: str | None, in_lambda: bool) -> str | None:
        if node is None:
            return None
        kind = node.type
        if kind == "identifier":
            name = _text(node, source)
            if name in env:
                return env[name]
            if name[:1].isupper() and resolver.class_of(name) is not None:
                return name  # `Foo.member`: the class (or its companion) itself
            if cls and not in_lambda:
                hit = resolver.find(cls, name, "prop")
                if hit is not None:
                    return hit[1]
            return None
        if kind == "this_expression":
            return None if in_lambda else cls
        if kind in ("parenthesized_expression", "unary_expression", "postfix_expression"):
            inner = [c for c in node.children if c.type not in ("(", ")", "!!", "!", "-", "+")]
            return infer(inner[0], env, cls, in_lambda) if len(inner) == 1 else None
        if kind in ("as_expression",):
            target = next((c for c in node.children if c.type in ("user_type", "nullable_type")), None)
            return type_name(target, source)
        if kind == "navigation_expression":
            left, member = _split_navigation(node)
            if member is None:
                return None
            owner_type = infer(left, env, cls, in_lambda)
            hit = resolver.find(owner_type, _text(member, source), "prop")
            return hit[1] if hit is not None else None
        if kind == "call_expression":
            callee = node.children[0] if node.children else None
            if callee is None:
                return None
            if callee.type == "identifier":
                name = _text(callee, source)
                if name[:1].isupper() and resolver.class_of(name) is not None:
                    return name  # constructor call
                if cls and not in_lambda:
                    hit = resolver.find(cls, name, "fun")
                    if hit is not None:
                        return hit[1]
                return None
            if callee.type == "navigation_expression":
                left, member = _split_navigation(callee)
                if member is None:
                    return None
                hit = resolver.find(infer(left, env, cls, in_lambda), _text(member, source), "fun")
                return hit[1] if hit is not None else None
        return None

    def declare_params(fn: Node, env: dict) -> None:
        params = next((c for c in fn.children if c.type == "function_value_parameters"), None)
        for prm in (params.children if params is not None else []):
            if prm.type == "parameter":
                ident = next((c for c in prm.children if c.type == "identifier"), None)
                if ident is not None:
                    env[_text(ident, source)] = type_name(next((c for c in prm.children if c.type in ("user_type", "nullable_type")), None), source)

    def walk(node: Node, env: dict, cls: str | None, in_lambda: bool) -> None:
        kind = node.type
        if kind in _CLASS_NODES:
            ident = next((c for c in node.children if c.type == "identifier"), None)
            name = _text(ident, source) if ident is not None else None
            class_env: dict = {}
            ctor = next((c for c in node.children if c.type == "primary_constructor"), None)
            params = next((c for c in (ctor.children if ctor is not None else []) if c.type == "class_parameters"), None)
            for prm in (params.children if params is not None else []):
                if prm.type == "class_parameter":
                    pid = next((c for c in prm.children if c.type == "identifier"), None)
                    if pid is not None:
                        class_env[_text(pid, source)] = type_name(next((c for c in prm.children if c.type in ("user_type", "nullable_type")), None), source)
            for child in node.children:
                if child.type not in ("primary_constructor",) and child is not ident:
                    walk(child, class_env, name if name in own else None, False)
            return
        if kind == "function_declaration":
            fn_env = dict(env)
            declare_params(node, fn_env)
            for child in node.children:
                if child.type not in ("function_value_parameters", "identifier"):
                    walk(child, fn_env, cls, in_lambda)
            return
        if kind == "lambda_literal":
            for child in node.children:
                walk(child, {**env, "it": None}, cls, True)
            return
        if kind == "property_declaration":
            declared = None
            for variable in (c for c in node.children if c.type == "variable_declaration"):
                ident = next((c for c in variable.children if c.type == "identifier"), None)
                if ident is not None:
                    declared = _declared_type(variable, source)
                    value = None
                    seen_eq = False
                    for child in node.children:
                        if child.type == "=":
                            seen_eq = True
                        elif seen_eq:
                            value = child
                            break
                    guessed = declared or (infer(value, env, cls, in_lambda) if value is not None else None)
                    name = _text(ident, source)
                    env[name] = guessed if name not in env or env[name] == guessed else None
            for child in node.children:
                if child.type != "variable_declaration":
                    walk(child, env, cls, in_lambda)
            return
        if kind in ("parameter", "class_parameter", "type_parameter", "import", "package_header", "user_type", "annotation"):
            return
        if kind == "value_argument":
            kids = list(node.children)
            if len(kids) >= 3 and kids[0].type == "identifier" and kids[1].type == "=":
                kids = kids[2:]  # a named argument: the name is the callee's parameter
            for child in kids:
                walk(child, env, cls, in_lambda)
            return
        if kind == "navigation_expression":
            left, member = _split_navigation(node)
            if member is not None:
                owner_type = infer(left, env, cls, in_lambda)
                if owner_type is not None:
                    parent = node.parent
                    is_call = parent is not None and parent.type == "call_expression" and parent.children and parent.children[0].id == node.id
                    name = _text(member, source)
                    hit = resolver.find(owner_type, name, "fun" if is_call else "prop") or resolver.find(owner_type, name, "prop" if is_call else "fun")
                    if hit is not None:
                        note(hit[0])
            if left is not None:
                walk(left, env, cls, in_lambda)
            return
        if kind == "identifier":
            name = _text(node, source)
            if name not in env and cls and not in_lambda and name[:1].islower():
                for table in ("prop", "fun"):
                    hit = resolver.find(cls, name, table)
                    if hit is not None:
                        note(hit[0])
                        break
            return
        for child in node.children:
            walk(child, env, cls, in_lambda)

    walk(root, {}, None, False)
    return found


def _split_navigation(node: Node) -> tuple[Node | None, Node | None]:
    """(receiver expression, member identifier) of `a.b` / `a?.b`."""
    children = [c for c in node.children if c.type not in (".", "?.", "::")]
    if len(children) < 2:
        return None, None
    member = children[-1]
    if member.type == "navigation_suffix":
        member = next((c for c in member.children if c.type == "identifier"), None)
    return children[0], member if member is not None and member.type == "identifier" else None
