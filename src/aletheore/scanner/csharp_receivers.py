"""Static types of the receivers in `receiver.Method(...)` calls, from the grammar and the file's own declarations.

The compiler binds `x.Humanize()` to the extension overload whose `this` parameter accepts the static type of `x`.
Without type information every file that declares an extension method of that name looks like a dependency. This module
infers the receiver type only where it is certain (a literal, `new T()`, a declared local/parameter/field, a cast, an
enum member) and decides whether an overload's receiver type could accept it. It answers "definitely not" only in clear
cases; anything it cannot decide counts as compatible, so a missing type never removes an edge.
"""

from __future__ import annotations

import re

from tree_sitter import Node

_ALIASES = {
    "String": "string", "Int32": "int", "Int64": "long", "Int16": "short", "Byte": "byte", "SByte": "sbyte",
    "UInt32": "uint", "UInt64": "ulong", "UInt16": "ushort", "Double": "double", "Single": "float",
    "Decimal": "decimal", "Boolean": "bool", "Char": "char", "Object": "object",
}
_LEAF = {
    "string", "int", "long", "short", "byte", "sbyte", "uint", "ulong", "ushort", "double", "float", "decimal", "bool",
    "char", "DateTime", "DateTimeOffset", "TimeSpan", "Guid", "DateOnly", "TimeOnly", "Half", "Int128", "UInt128", "Version",
}
# static members whose type is fixed by the BCL: (type, member) -> the member's type
_STATIC_MEMBER_TYPES = {
    ("string", "Empty"): "string",
    ("DateTime", "Now"): "DateTime", ("DateTime", "UtcNow"): "DateTime", ("DateTime", "Today"): "DateTime",
    ("DateTime", "MinValue"): "DateTime", ("DateTime", "MaxValue"): "DateTime",
    ("DateTimeOffset", "Now"): "DateTimeOffset", ("DateTimeOffset", "UtcNow"): "DateTimeOffset",
    ("TimeSpan", "Zero"): "TimeSpan", ("TimeSpan", "MinValue"): "TimeSpan", ("TimeSpan", "MaxValue"): "TimeSpan",
    ("Guid", "Empty"): "Guid", ("DateOnly", "MinValue"): "DateOnly", ("DateOnly", "MaxValue"): "DateOnly",
}
_NUMERIC_MEMBERS = {"MaxValue", "MinValue"}
# implicit numeric conversions (C# spec 10.2.3)
_WIDENING = {
    "sbyte": {"short", "int", "long", "float", "double", "decimal"},
    "byte": {"short", "ushort", "int", "uint", "long", "ulong", "float", "double", "decimal"},
    "short": {"int", "long", "float", "double", "decimal"},
    "ushort": {"int", "uint", "long", "ulong", "float", "double", "decimal"},
    "int": {"long", "float", "double", "decimal"},
    "uint": {"long", "ulong", "float", "double", "decimal"},
    "long": {"float", "double", "decimal"},
    "ulong": {"float", "double", "decimal"},
    "char": {"ushort", "int", "uint", "long", "ulong", "float", "double", "decimal"},
    "float": {"double"},
}
# BCL types a plain value such as a string or a repo class is never an instance of (unless the repo class derives it)
_UNRELATED = {"Type", "MethodInfo", "PropertyInfo", "FieldInfo", "MemberInfo", "Stream", "Uri", "Task", "Exception", "Array"}
_STRING_LITERALS = {"string_literal", "interpolated_string_expression", "verbatim_string_literal", "raw_string_literal"}
_REPO_NON_INTERFACE = {"class", "struct", "enum", "record"}
_SCOPE_NODES = {
    "method_declaration", "constructor_declaration", "local_function_statement", "lambda_expression",
    "anonymous_method_expression", "accessor_declaration", "operator_declaration", "conversion_operator_declaration",
    "indexer_declaration", "destructor_declaration", "class_declaration", "struct_declaration", "interface_declaration",
    "record_declaration", "record_struct_declaration", "enum_declaration", "compilation_unit",
}


def normalize_type(text: str) -> str:
    """`System.Collections.Generic.List<string>?` -> `List`; `Int32` -> `int`; `int[]` stays `int[]`."""
    text = text.strip().rstrip("?")
    array = ""
    while text.endswith("]"):
        cut = text.rfind("[")
        if cut < 0:
            break
        array += "[]"
        text = text[:cut].strip()
    text = re.sub(r"<.*>", "", text, flags=re.S).strip().rstrip("?")
    text = text.rsplit(".", 1)[-1]
    return _ALIASES.get(text, text) + (array and "[]")


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode(errors="ignore")


def _type_child(node: Node) -> Node | None:
    for child in node.children:
        if child.type in ("identifier", "predefined_type", "generic_name", "qualified_name", "array_type",
                          "nullable_type", "implicit_type", "tuple_type", "pointer_type"):
            return child
    return None


def definitely_not(recv: str, param: str, type_params: set[str], kinds: dict[str, str], ancestors) -> bool:
    """True only when a value of static type `recv` can certainly not be passed as an extension's `this` parameter of
    type `param`. `kinds` maps repo type names to class/struct/interface/enum/record; `ancestors(name)` is the set of
    base names (transitive) a repo type lists."""
    param = normalize_type(param)
    recv = normalize_type(recv)
    if not param or not recv or param in type_params or param == "object" or recv == param:
        return False
    recv_kind, param_kind = kinds.get(recv), kinds.get(param)
    if recv in _LEAF:
        if param in _LEAF:
            return param not in _WIDENING.get(recv, ())
        return param_kind in _REPO_NON_INTERFACE or param in _UNRELATED
    if recv.endswith("[]"):
        return param in _LEAF or param_kind in _REPO_NON_INTERFACE
    if recv_kind in _REPO_NON_INTERFACE:
        if param in _LEAF:
            return True
        if param_kind is not None:
            return param not in ancestors(recv)
        if param in _UNRELATED:
            return param not in ancestors(recv)
    return False


class ReceiverTypes:
    """Receiver static types of the method calls in one file: name -> [type or None] (None = could not be inferred)."""

    def __init__(self, root: Node, source: bytes, kinds: dict[str, str], ext_returns: dict[str, str] | None = None):
        self.source = source
        self.kinds = kinds
        self.ext_returns = ext_returns or {}  # extension method name -> the one return type all its overloads share
        self.local_returns: dict[str, set[str]] = {}  # method name -> return types of the methods declared in this file
        self._collect_local_returns(root)
        self.env: dict[int, dict[str, set[str | None]]] = {}  # scope node id -> declared name -> types
        self._collect_declarations(root)
        self.calls: dict[str, list[str | None]] = {}
        self._collect_calls(root)

    def _collect_local_returns(self, root: Node) -> None:
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "method_declaration":
                name_node, returns = n.child_by_field_name("name"), n.child_by_field_name("returns")
                if name_node is not None and returns is not None:
                    self.local_returns.setdefault(_text(name_node, self.source), set()).add(normalize_type(_text(returns, self.source)))
            stack.extend(n.children)

    # --- declared types -------------------------------------------------------------------------------------------
    @staticmethod
    def _scope_of(node: Node) -> int:
        """The innermost method, lambda, accessor or type declaration around `node` (a name declared there is visible
        in all of it; the same name in another method is a different variable)."""
        cursor = node.parent
        while cursor is not None and cursor.type not in _SCOPE_NODES:
            cursor = cursor.parent
        return cursor.id if cursor is not None else -1

    def _declare(self, node: Node, name: str, type_name: str | None) -> None:
        self.env.setdefault(self._scope_of(node), {}).setdefault(name, set()).add(type_name)

    def _lookup(self, node: Node, name: str) -> str | None:
        """Type of the variable `name` as seen from `node`: the nearest enclosing scope that declares it."""
        cursor: Node | None = node
        while cursor is not None:
            if cursor.type in _SCOPE_NODES:
                declared = self.env.get(cursor.id, {}).get(name)
                if declared is not None:
                    return next(iter(declared)) if len(declared) == 1 else None
            cursor = cursor.parent
        return None

    def _declared_anywhere(self, node: Node, name: str) -> bool:
        cursor: Node | None = node
        while cursor is not None:
            if cursor.type in _SCOPE_NODES and name in self.env.get(cursor.id, {}):
                return True
            cursor = cursor.parent
        return False

    def _collect_declarations(self, root: Node) -> None:
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "using_directive":
                continue
            kind = n.type
            if kind in ("parameter", "catch_declaration", "declaration_pattern"):
                type_node = _type_child(n)
                name_node = n.child_by_field_name("name") or next((c for c in reversed(n.children) if c.type == "identifier"), None)
                if type_node is not None and name_node is not None and type_node.id != name_node.id:
                    self._declare(name_node, _text(name_node, self.source), normalize_type(_text(type_node, self.source)))
            elif kind in ("field_declaration", "event_field_declaration"):
                for child in n.children:
                    if child.type == "variable_declaration":
                        self._variable_declaration(child)
            elif kind == "property_declaration":
                type_node = n.child_by_field_name("type")
                name_node = n.child_by_field_name("name")
                if type_node is not None and name_node is not None:
                    self._declare(name_node, _text(name_node, self.source), normalize_type(_text(type_node, self.source)))
            elif kind == "variable_declaration":
                self._variable_declaration(n)
            elif kind in ("lambda_expression", "anonymous_method_expression"):
                # a parameter without a declared type (`x => ...`, `(a, b) => ...`) hides any outer variable of that name
                for child in n.children:
                    candidates = [child] if child.type == "implicit_parameter" else (
                        [prm for prm in child.children if prm.type == "parameter"] if child.type == "parameter_list" else []
                    )
                    for prm in candidates:
                        if prm.type == "implicit_parameter":
                            self.env.setdefault(n.id, {}).setdefault(_text(prm, self.source).strip(), set()).add(None)
                            continue
                        idents = [c for c in prm.children if c.type == "identifier"]
                        if idents and len(prm.children) == len(idents):  # no type written: only the name
                            self.env.setdefault(n.id, {}).setdefault(_text(idents[-1], self.source), set()).add(None)
            elif kind == "foreach_statement":
                left = n.child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    self._declare(left, _text(left, self.source), None)  # element type needs the collection's type
            stack.extend(n.children)

    def _variable_declaration(self, node: Node) -> None:
        type_node = node.child_by_field_name("type") or _type_child(node)
        if type_node is None:
            return
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name") or next((c for c in declarator.children if c.type == "identifier"), None)
            if name_node is None:
                continue
            name = _text(name_node, self.source)
            if type_node.type == "implicit_type":
                value = next((c for c in declarator.children if c.type not in ("identifier", "=", "equals_value_clause")), None)
                eq = next((c for c in declarator.children if c.type == "equals_value_clause"), None)
                if eq is not None:
                    value = next((c for c in eq.children if c.type != "="), None)
                self._declare(name_node, name, self.infer(value) if value is not None else None)
            else:
                self._declare(name_node, name, normalize_type(_text(type_node, self.source)))

    # --- expression types -----------------------------------------------------------------------------------------
    def infer(self, node: Node | None) -> str | None:
        if node is None:
            return None
        kind = node.type
        if kind in _STRING_LITERALS:
            return "string"
        if kind == "integer_literal":
            t = _text(node, self.source).lower()
            return "ulong" if t.endswith(("ul", "lu")) else "long" if t.endswith("l") else "uint" if t.endswith("u") else "int"
        if kind == "real_literal":
            t = _text(node, self.source).lower()
            return "float" if t.endswith("f") else "decimal" if t.endswith("m") else "double"
        if kind == "boolean_literal":
            return "bool"
        if kind == "character_literal":
            return "char"
        if kind == "parenthesized_expression":
            return self.infer(next((c for c in node.children if c.type not in ("(", ")")), None))
        if kind in ("cast_expression", "as_expression"):
            type_node = node.child_by_field_name("type")
            return normalize_type(_text(type_node, self.source)) if type_node is not None else None
        if kind == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            return normalize_type(_text(type_node, self.source)) if type_node is not None else None
        if kind == "identifier":
            return self._lookup(node, _text(node, self.source))
        if kind == "binary_expression":
            return self._binary(node)
        if kind in ("prefix_unary_expression", "postfix_unary_expression"):
            operand = next((c for c in node.children if c.type not in ("!", "-", "+", "~", "++", "--")), None)
            op = next((c.type for c in node.children if c.type in ("!", "-", "+", "~", "++", "--")), "")
            if op == "!" and kind == "prefix_unary_expression":
                return "bool"
            return self.infer(operand)  # `-x`, `x++`, `x!` keep the operand's type
        if kind == "conditional_expression":
            branches = [self.infer(c) for c in node.children if c.type not in ("?", ":")][1:]
            return branches[0] if len(branches) == 2 and branches[0] == branches[1] else None
        if kind == "element_access_expression":
            base = self.infer(node.child_by_field_name("expression"))
            if base == "string":
                return "char"
            return base[:-2] if base and base.endswith("[]") else None
        if kind == "invocation_expression":
            return self._invocation(node)
        if kind == "member_access_expression":
            left = node.child_by_field_name("expression")
            member = node.child_by_field_name("name")
            if left is not None and member is not None and left.type in ("predefined_type", "identifier"):
                owner = normalize_type(_text(left, self.source))
                member_name = _text(member, self.source)
                if not self._declared_anywhere(node, _text(left, self.source)):
                    if (owner, member_name) in _STATIC_MEMBER_TYPES:
                        return _STATIC_MEMBER_TYPES[(owner, member_name)]
                    if owner in _LEAF and owner not in ("string", "bool", "DateTime", "DateTimeOffset", "TimeSpan", "Guid") \
                            and member_name in _NUMERIC_MEMBERS:
                        return owner  # `int.MaxValue` is an int
            if left is not None and left.type == "identifier" and self.kinds.get(_text(left, self.source)) == "enum":
                if not self._declared_anywhere(node, _text(left, self.source)):
                    return _text(left, self.source)  # `Color.Red` is a Color
        return None

    _STRING_RESULTS = {"Trim", "TrimStart", "TrimEnd", "ToLower", "ToUpper", "ToLowerInvariant", "ToUpperInvariant",
                       "Substring", "Replace", "Remove", "Insert", "PadLeft", "PadRight", "Normalize"}
    _RANK = {"double": 4, "float": 3, "decimal": 5, "ulong": 2, "long": 2, "uint": 1, "int": 1, "short": 1, "ushort": 1,
             "byte": 1, "sbyte": 1, "char": 1}

    def _binary(self, node: Node) -> str | None:
        op_node = node.child_by_field_name("operator")
        op = _text(op_node, self.source) if op_node is not None else ""
        if op in ("==", "!=", "<", ">", "<=", ">=", "&&", "||", "is"):
            return "bool"
        left, right = self.infer(node.child_by_field_name("left")), self.infer(node.child_by_field_name("right"))
        if op == "+" and "string" in (left, right):
            return "string"
        if op in ("+", "-", "*", "/", "%") and left in self._RANK and right in self._RANK:
            if left == right and left not in ("char", "short", "ushort", "byte", "sbyte"):
                return left
            wide = {"decimal": "decimal", "double": "double", "float": "float"}
            for name in ("decimal", "double", "float"):
                if name in (left, right):
                    return wide[name]
            if "ulong" in (left, right):
                return "ulong" if {left, right} <= {"ulong", "uint", "ushort", "byte"} else None
            if "long" in (left, right):
                return "long"
            if "uint" in (left, right):
                return "uint" if {left, right} <= {"uint", "ushort", "byte"} else "long"
            return "int"  # int, short, byte, char operands promote to int
        return None

    def _invocation(self, node: Node) -> str | None:
        func = node.child_by_field_name("function")
        if func is None:
            return None
        if func.type == "member_access_expression":
            name_node = func.child_by_field_name("name")
            recv = self.infer(func.child_by_field_name("expression"))
        elif func.type == "identifier":
            name_node, recv = func, None
        else:
            return None
        if name_node is not None and name_node.type == "generic_name":
            name_node = next((c for c in name_node.children if c.type == "identifier"), None)
        if name_node is None:
            return None
        name = _text(name_node, self.source)
        if name == "ToString":
            return "string"
        if recv == "string" and name in self._STRING_RESULTS:
            return "string"
        if func.type == "identifier":
            local = self.local_returns.get(name)
            if local and len(local) == 1:
                value = next(iter(local))
                return value if value not in ("void", "var") else None
            return None
        # a chained extension call: every extension overload of that name returns the same type
        return self.ext_returns.get(name)

    # --- call sites -----------------------------------------------------------------------------------------------
    def _collect_calls(self, root: Node) -> None:
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "using_directive":
                continue
            if n.type == "invocation_expression":
                func = n.child_by_field_name("function")
                if func is not None and func.type == "member_access_expression":
                    name_node = func.child_by_field_name("name")
                    if name_node is not None and name_node.type == "generic_name":
                        name_node = next((c for c in name_node.children if c.type == "identifier"), None)
                    if name_node is not None:
                        recv_node = func.child_by_field_name("expression")
                        recv = self.infer(recv_node)
                        if recv is None and recv_node is not None and recv_node.type == "identifier":
                            # a static call on a type name (`Extensions.M(x)`) is not an extension-method call
                            if _text(recv_node, self.source) in self.kinds:
                                recv = "<type>"
                        self.calls.setdefault(_text(name_node, self.source), []).append(recv)
            stack.extend(reversed(n.children))
