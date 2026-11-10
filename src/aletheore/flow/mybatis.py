"""MyBatis mappers: `${name}` in a mapper's SQL is pasted into the statement text, unlike `#{name}`, which is bound.

A call to such a mapper method is a SQL sink at the parameters its `${...}` placeholders name. The SQL lives in a
`@Select/@Update/@Insert/@Delete` annotation on the interface method or in a mapper XML file whose namespace names
the interface. Returns {interface simple name: {method name: parameter positions}}.
"""

from __future__ import annotations

import re
from pathlib import Path

from tree_sitter import Node

_ANNOT = {"Select", "Update", "Insert", "Delete"}
_DOLLAR = re.compile(r"\$\{\s*([A-Za-z_][\w.]*)")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_NAMESPACE = re.compile(r"<mapper\b[^>]*\bnamespace\s*=\s*[\"']([^\"']+)[\"']")
_STMT = re.compile(r"<(select|update|insert|delete|sql)\b([^>]*)>(.*?)</\1\s*>", re.S)
_ID = re.compile(r"\bid\s*=\s*[\"']([^\"']+)[\"']")
_SKIP = {"node_modules", "target", "build", ".git", "test", "tests"}


def _t(n: Node | None) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _params(method: Node) -> list[tuple[str, str | None]]:
    """(parameter name, @Param value) in order."""
    out = []
    plist = method.child_by_field_name("parameters")
    for p in (plist.named_children if plist is not None else []):
        if p.type not in ("formal_parameter", "spread_parameter"):
            continue
        name = _t(p.child_by_field_name("name"))
        alias = None
        mods = next((c for c in p.children if c.type == "modifiers"), None)
        for a in (mods.named_children if mods is not None else []):
            if a.type == "annotation" and _t(a.child_by_field_name("name")).split(".")[-1] == "Param":
                m = re.search(r"[\"']([^\"']+)[\"']", _t(a.child_by_field_name("arguments")))
                alias = m.group(1) if m else None
        out.append((name, alias))
    return out


def _positions(names: set[str], params: list[tuple[str, str | None]] | None) -> list[int]:
    if params is None:
        return list(range(8))  # interface not found: judge every argument
    pos: set[int] = set()
    for nm in names:
        root = nm.split(".")[0]
        hit = [i for i, (n, a) in enumerate(params) if root in (a, n)]
        if hit:
            pos.update(hit)
        elif root in ("_parameter", "value") or len(params) == 1:
            pos.add(0)  # the single parameter, or a property of it (`${orderBy}` on a DTO)
        else:
            pos.update(range(len(params)))
    return sorted(pos)


def _annotation_sql(method: Node) -> str | None:
    mods = next((c for c in method.children if c.type == "modifiers"), None)
    for a in (mods.named_children if mods is not None else []):
        if a.type == "annotation" and _t(a.child_by_field_name("name")).split(".")[-1] in _ANNOT:
            return _t(a.child_by_field_name("arguments"))
    return None


def mybatis_sinks(repo: Path, index) -> dict[str, dict[str, list[int]]]:
    out: dict[str, dict[str, list[int]]] = {}
    ifaces: dict[str, dict[str, Node]] = {}
    for p in repo.rglob("*.java"):
        if _SKIP.intersection(p.relative_to(repo).parts):
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if b"interface" not in data or not (b"ibatis" in data or b"@Mapper" in data or b"${" in data):
            continue
        jf = index.file(p)
        if jf is None:
            continue
        for cls in jf.classes:
            if cls.node.type != "interface_declaration":
                continue
            ifaces[cls.name] = {nm: ms[0] for nm, ms in cls.methods.items() if ms}
            for nm, ms in cls.methods.items():
                for m in ms:
                    sql = _annotation_sql(m)
                    names = set(_DOLLAR.findall(sql)) if sql else set()
                    if names:
                        out.setdefault(cls.name, {})[nm] = _positions(names, _params(m))
    for p in repo.rglob("*.xml"):
        if _SKIP.intersection(p.relative_to(repo).parts):
            continue
        try:
            text = p.read_text("utf-8", "replace")
        except OSError:
            continue
        if "<mapper" not in text or "${" not in text:
            continue
        text = _COMMENT.sub("", text)
        ns = _NAMESPACE.search(text)
        if not ns:
            continue
        iface = ns.group(1).split(".")[-1]
        for _kind, attrs, body in _STMT.findall(text):
            mid = _ID.search(attrs)
            names = set(_DOLLAR.findall(body))
            if mid and names:
                meth = ifaces.get(iface, {}).get(mid.group(1))
                out.setdefault(iface, {})[mid.group(1)] = _positions(names, _params(meth) if meth is not None else None)
    if out:
        # every mapper method, safe ones too, so a name shared with a safe mapper is never matched by name alone
        out["*methods*"] = {}
        for iface, methods in ifaces.items():
            for m in methods:
                out["*methods*"].setdefault(m, set()).add(iface)
    return out


def positions(table: dict[str, dict[str, list[int]]], name: str, recv_type: str | None, recv_text: str) -> list[int] | None:
    """Sink positions for a call `recv.name(...)` on a mapper, or None."""
    every = table.get("*methods*", {})
    if recv_type:
        return table[recv_type].get(name) if recv_type in table and recv_type != "*methods*" else None
    owners = [(i, m[name]) for i, m in table.items() if i != "*methods*" and name in m]
    low = recv_text.lower()
    # the receiver's type is unknown: only a method name that no other mapper declares, on a mapper-looking receiver
    if len(owners) == 1 and len(every.get(name, {owners[0][0]})) == 1 and any(h in low for h in ("mapper", "dao", "repository", "repo")):
        return owners[0][1]
    return None
