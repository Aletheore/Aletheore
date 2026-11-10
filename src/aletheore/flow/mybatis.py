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
    # every mapper XML first: a statement can include a `<sql>` fragment from another mapper's namespace
    mappers: list[tuple[Path, str, list[tuple[str, str, str]]]] = []
    fragments: dict[str, list[tuple[str, str]]] = {}  # "namespace.id" -> [(namespace, body)]
    any_dollar = False
    for p in repo.rglob("*.xml"):
        if _SKIP.intersection(p.relative_to(repo).parts):
            continue
        try:
            text = p.read_text("utf-8", "replace")
        except OSError:
            continue
        if "<mapper" not in text:
            continue
        text = _COMMENT.sub("", text)
        ns = _NAMESPACE.search(text)
        if not ns:
            continue
        any_dollar = any_dollar or "${" in text
        stmts = _STMT.findall(text)
        mappers.append((p, ns.group(1), stmts))
        for kind, attrs, body in stmts:
            fid = _ID.search(attrs)
            if kind == "sql" and fid:
                fragments.setdefault(_fragment_key(fid.group(1), ns.group(1)), []).append((ns.group(1), body))
    for p, namespace, stmts in (mappers if any_dollar else []):
        iface = namespace.split(".")[-1]
        for kind, attrs, body in stmts:
            if kind == "sql":
                continue
            state = _Expansion()
            body = _expand_includes(body, namespace, namespace, {}, fragments, (), state)
            mid = _ID.search(attrs)
            names = set(_DOLLAR.findall(body))
            if state.truncated:
                from aletheore.flow.coverage import note
                note(p)
                names.add("*unexpanded*")  # an include chain past the safety cap: keep the call as a sink
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


_INCLUDE = re.compile(r"<include\b([^>]*?)(?:/>|>(.*?)</include\s*>)", re.S)
_REFID = re.compile(r"\brefid\s*=\s*[\"']([^\"']+)[\"']")
_PROPERTY = re.compile(r"<property\b([^>]*?)/?>")
_PNAME = re.compile(r"\bname\s*=\s*[\"']([^\"']*)[\"']")
_PVALUE = re.compile(r"\bvalue\s*=\s*[\"']([^\"']*)[\"']")
_VAR = re.compile(r"\$\{([^}]*)\}")
_MAX_DEPTH = 64  # far past any real mapper; only a pathological chain reaches it
_MAX_CHARS = 2_000_000


class _Expansion:
    def __init__(self) -> None:
        self.truncated = False
        self.chars = 0


def _fragment_key(fid: str, namespace: str) -> str:
    """MyBatis stores a `<sql id>` as `namespace.id` (an id already prefixed by its own namespace is kept)."""
    return fid if fid.startswith(namespace + ".") else f"{namespace}.{fid}"


def _resolve(refid: str, ns_stmt: str, ns_here: str, fragments: dict) -> list[str]:
    """Fragment keys an include names. As in MyBatis, a refid with a dot is fully qualified and one without gets the
    current namespace. Inside another namespace's fragment the "current" namespace is ambiguous (MyBatis uses the
    including mapper's), so both are tried: unknown means keep."""
    if "." in refid:
        if refid in fragments:
            return [refid]
        return [k for k in fragments if k.endswith("." + refid)]  # a refid written with a shortened namespace
    return [k for k in dict.fromkeys((f"{ns_stmt}.{refid}", f"{ns_here}.{refid}")) if k in fragments]


def _subst(text: str, ctx: dict[str, str]) -> str:
    """MyBatis replaces `${name}` in included text with the include's `<property>` values at load time; a name that
    is not a property stays and is decided at run time."""
    return _VAR.sub(lambda m: ctx.get(m.group(1).strip(), m.group(0)), text) if ctx else text


def _expand_includes(body: str, ns_stmt: str, ns_here: str, ctx: dict[str, str], fragments: dict,
                     stack: tuple[str, ...], state: _Expansion) -> str:
    """Inline every `<include refid>` with the `<sql>` fragment it names, in any mapper's namespace and nested to any
    depth, after substituting the include's `<property>` values, so a `${}` in a shared fragment is seen in every
    statement that includes it and one fixed by a property is not. A cycle (which MyBatis rejects) is cut."""
    if "<include" not in body:
        return body

    def one(m: re.Match) -> str:
        r = _REFID.search(m.group(1))
        if r is None:
            return ""
        refid = _subst(r.group(1), ctx).strip()
        props = dict(ctx)
        for pm in _PROPERTY.finditer(m.group(2) or ""):
            n, v = _PNAME.search(pm.group(1)), _PVALUE.search(pm.group(1))
            if n is not None and v is not None:
                props[n.group(1)] = _subst(v.group(1), ctx)
        parts = []
        for key in _resolve(refid, ns_stmt, ns_here, fragments):
            if key in stack:
                continue
            if len(stack) >= _MAX_DEPTH or state.chars > _MAX_CHARS:
                state.truncated = True
                continue
            for fns, fbody in fragments[key]:
                state.chars += len(fbody)
                parts.append(_expand_includes(_subst(fbody, props), ns_stmt, fns, props, fragments, stack + (key,), state))
        return " ".join(parts)

    return _INCLUDE.sub(one, body)


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
