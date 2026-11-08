"""A lazy index of JavaScript and TypeScript modules: functions, classes, exports and requires.

`require("./dao")` and `import x from "./dao"` resolve to repository files; `module.exports`,
`exports.name = ...` and `export` statements say what each file offers. Package imports (anything not
starting with a dot) resolve to nothing, which the interpreter treats as unknown.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.graph import JS_LANGUAGE, TS_LANGUAGE, TSX_LANGUAGE

EXTS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
FN_TYPES = ("function_declaration", "function_expression", "function", "arrow_function", "generator_function_declaration",
            "generator_function")


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


@dataclass(eq=False)
class JsClass:
    name: str
    node: Node
    file: "JsFile"
    superclass: str | None = None
    methods: dict[str, Node] = field(default_factory=dict)


@dataclass(eq=False)
class JsFile:
    path: Path
    src: bytes
    root: Node
    functions: dict[str, Node] = field(default_factory=dict)       # name -> function node (declared or assigned)
    classes: dict[str, JsClass] = field(default_factory=dict)
    exports: dict[str, Node] = field(default_factory=dict)         # exported name -> function/class/value node
    requires: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # local -> (module spec, member)
    constants: dict[str, Node] = field(default_factory=dict)


def _fn_value(n: Node | None) -> Node | None:
    return n if n is not None and n.type in FN_TYPES else None


def _scan(f: JsFile) -> None:
    stack = [f.root]
    while stack:
        n = stack.pop()
        t = n.type
        if t == "function_declaration" or t == "generator_function_declaration":
            f.functions[_t(n.child_by_field_name("name"))] = n
        elif t == "class_declaration" or t == "class":
            name = _t(n.child_by_field_name("name"))
            if name:
                cls = JsClass(name, n, f)
                for c in n.children:
                    if c.type == "class_heritage":
                        cls.superclass = _t(c.named_children[0]).split(".")[-1] if c.named_children else None
                body = n.child_by_field_name("body")
                if body is not None:
                    for m in body.named_children:
                        if m.type == "method_definition":
                            cls.methods[_t(m.child_by_field_name("name"))] = m
                f.classes[name] = cls
        elif t == "variable_declarator":
            name_n, val = n.child_by_field_name("name"), n.child_by_field_name("value")
            if name_n is not None and name_n.type == "identifier" and val is not None:
                if _fn_value(val) is not None:
                    f.functions[_t(name_n)] = val
                elif val.type == "call_expression" and _t(val.child_by_field_name("function")) == "require":
                    args = val.child_by_field_name("arguments")
                    spec = next((_t(a).strip("'\"`") for a in (args.named_children if args else []) if a.type == "string"), None)
                    if spec:
                        f.requires[_t(name_n)] = (spec, None)
                elif val.type == "member_expression" and val.child_by_field_name("object") is not None \
                        and val.child_by_field_name("object").type == "call_expression" \
                        and _t(val.child_by_field_name("object").child_by_field_name("function")) == "require":
                    # const Dao = require("./dao").Dao
                    args = val.child_by_field_name("object").child_by_field_name("arguments")
                    spec = next((_t(a).strip("'\"`") for a in (args.named_children if args else []) if a.type == "string"), None)
                    if spec:
                        f.requires[_t(name_n)] = (spec, _t(val.child_by_field_name("property")))
                elif val.type in ("string", "number", "template_string", "true", "false"):
                    f.constants[_t(name_n)] = val
                elif val.type == "array" and not re.search(rb"\b" + re.escape(name_n.text) + rb"\s*(\.(push|unshift|splice|concat|fill|pop|shift)\(|\[[^\]]*\]\s*=[^=])", f.src):
                    f.constants[_t(name_n)] = val  # a module-level list nothing in the file adds to: an allowlist
            elif name_n is not None and name_n.type == "object_pattern" and val is not None \
                    and val.type == "call_expression" and _t(val.child_by_field_name("function")) == "require":
                args = val.child_by_field_name("arguments")
                spec = next((_t(a).strip("'\"`") for a in (args.named_children if args else []) if a.type == "string"), None)
                if spec:
                    for p in name_n.named_children:
                        if p.type == "shorthand_property_identifier_pattern":
                            f.requires[_t(p)] = (spec, _t(p))
                        elif p.type == "pair_pattern":
                            key = _t(p.child_by_field_name("key"))
                            local = _t(p.child_by_field_name("value"))
                            f.requires[local] = (spec, key)
        elif t == "import_statement":
            src_n = n.child_by_field_name("source")
            spec = _t(src_n).strip("'\"`") if src_n is not None else ""
            for c in n.named_children:
                if c.type == "import_clause":
                    for x in c.named_children:
                        if x.type == "identifier":
                            f.requires[_t(x)] = (spec, "default")
                        elif x.type == "named_imports":
                            for sp in x.named_children:
                                nm = sp.child_by_field_name("name")
                                al = sp.child_by_field_name("alias")
                                f.requires[_t(al) if al is not None else _t(nm)] = (spec, _t(nm))
                        elif x.type == "namespace_import" and x.named_children:
                            f.requires[_t(x.named_children[0])] = (spec, None)
        elif t == "assignment_expression":
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            lt = _t(left)
            if lt == "module.exports" and right is not None:
                if right.type == "object":
                    for p in right.named_children:
                        if p.type == "shorthand_property_identifier":
                            f.exports[_t(p)] = p
                        elif p.type == "pair":
                            f.exports[_t(p.child_by_field_name("key"))] = p.child_by_field_name("value")
                elif right.type == "identifier":
                    f.exports["default"] = right
                elif right.type in FN_TYPES or right.type == "class":
                    f.exports["default"] = right
            elif lt.startswith("module.exports.") or lt.startswith("exports."):
                f.exports[lt.split(".")[-1]] = right
        elif t == "export_statement":
            for c in n.named_children:
                if c.type in ("function_declaration", "class_declaration", "generator_function_declaration"):
                    f.exports[_t(c.child_by_field_name("name"))] = c
                elif c.type == "lexical_declaration":
                    for d in c.named_children:
                        if d.type == "variable_declarator":
                            f.exports[_t(d.child_by_field_name("name"))] = d.child_by_field_name("value")
                elif c.type == "identifier":
                    f.exports["default"] = c
                elif c.type == "export_clause":
                    for sp in c.named_children:
                        nm = sp.child_by_field_name("name")
                        al = sp.child_by_field_name("alias")
                        f.exports[_t(al) if al is not None else _t(nm)] = nm
        stack.extend(n.children)


class JsIndex:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self._files: dict[Path, JsFile] = {}
        self._parsers = {".ts": Parser(TS_LANGUAGE), ".tsx": Parser(TSX_LANGUAGE)}
        self._js = Parser(JS_LANGUAGE)

    def file(self, path: Path) -> JsFile | None:
        path = path.resolve()
        if path in self._files:
            return self._files[path]
        try:
            src = path.read_bytes()
        except OSError:
            return None
        parser = self._parsers.get(path.suffix, self._js)
        jf = JsFile(path=path, src=src, root=parser.parse(src).root_node)
        _scan(jf)
        self._files[path] = jf
        return jf

    def resolve(self, spec: str, from_file: JsFile) -> JsFile | None:
        if not spec.startswith("."):
            return None
        base = (from_file.path.parent / spec)
        for cand in [base] + [Path(str(base) + e) for e in EXTS] + [base / ("index" + e) for e in EXTS]:
            if cand.is_file():
                return self.file(cand)
        return None

    def named_in(self, jf: JsFile, name: str):
        """A function or class node called `name` offered by `jf` (exported, or declared in it)."""
        node = jf.exports.get(name)
        if node is not None:
            if node.type == "identifier":
                return self.local(jf, _t(node))
            return ("class", self._class_of(jf, node)) if node.type in ("class", "class_declaration") else ("function", node) if node.type in FN_TYPES else None
        return self.local(jf, name)

    def local(self, jf: JsFile, name: str):
        if name in jf.functions:
            return ("function", jf.functions[name])
        if name in jf.classes:
            return ("class", jf.classes[name])
        return None

    @staticmethod
    def _class_of(jf: JsFile, node: Node):
        for c in jf.classes.values():
            if c.node is node:
                return c
        name = _t(node.child_by_field_name("name"))
        return jf.classes.get(name)

    def lookup(self, name: str, from_file: JsFile):
        """Resolve a bare name through declarations and requires: ("function"|"class", node|JsClass, file)."""
        hit = self.local(from_file, name)
        if hit:
            return hit[0], hit[1], from_file
        req = from_file.requires.get(name)
        if req and req[1]:
            mod = self.resolve(req[0], from_file)
            if mod is not None:
                hit = self.named_in(mod, "default" if req[1] == "default" else req[1])
                if hit:
                    return hit[0], hit[1], mod
        return None

    def module_member(self, base: str, member: str, from_file: JsFile):
        """`dao.find` where `dao = require("./dao")`: ("function"|"class", node|JsClass, file)."""
        req = from_file.requires.get(base)
        if not req or req[1] not in (None, "default"):
            return None
        mod = self.resolve(req[0], from_file)
        if mod is None:
            return None
        hit = self.named_in(mod, member)
        return (hit[0], hit[1], mod) if hit else None
