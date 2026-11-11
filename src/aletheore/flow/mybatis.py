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
    declared: dict[str, set[tuple[str, int]]] = {}  # (method name, parameter count) each interface declares itself
    parents: dict[str, tuple[list[str], object, object]] = {}
    for p in repo.rglob("*.java"):
        if _SKIP.intersection(p.relative_to(repo).parts):
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        # (an interface with no MyBatis marker of its own may still extend a mapper that has one)
        if b"interface" not in data or not (b"ibatis" in data or b"@Mapper" in data or b"${" in data or b"extends" in data):
            continue
        jf = index.file(p)
        if jf is None:
            continue
        pkg = _scope(jf)[0]
        for cls in jf.classes:
            if cls.node.type != "interface_declaration":
                continue
            fqn = _fqn(cls, pkg)
            ifaces[fqn] = {nm: ms[0] for nm, ms in cls.methods.items() if ms}
            declared[fqn] = {(nm, len(_params(m))) for nm, ms in cls.methods.items() for m in ms}
            parents[fqn] = (_extended(cls.node), jf, cls)
            for nm, ms in cls.methods.items():
                for m in ms:
                    sql = _annotation_sql(m)
                    names = set(_DOLLAR.findall(sql)) if sql else set()
                    if names:
                        out.setdefault(fqn, {})[nm] = _positions(names, _params(m))
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
        iface = namespace.replace("$", ".")
        if iface not in ifaces:  # a namespace written without the package: the one interface of that simple name
            same = [f for f in ifaces if f.rsplit(".", 1)[-1] == iface.rsplit(".", 1)[-1]]
            iface = same[0] if len(same) == 1 else iface
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
    inherited = _inherit(out, ifaces, declared, parents)
    if out:
        # every mapper method, safe ones too, so a name shared with a safe mapper is never matched by name alone
        out["*methods*"] = {}
        for iface, methods in ifaces.items():
            for m in set(methods) | inherited.get(iface, set()):
                out["*methods*"].setdefault(m, set()).add(iface)
        # simple name -> qualified names, so a receiver typed `UserMapper` is resolved against the caller's package
        out["*simple*"] = {}
        # (every mapper XML namespace too, safe ones included: its interface may carry no MyBatis marker)
        known = set(ifaces) | {k for k in out if not k.startswith("*")} | {ns.replace("$", ".") for _, ns, _ in mappers}
        for fqn in known:
            out["*simple*"].setdefault(fqn.rsplit(".", 1)[-1], set()).add(fqn)
    return out


def _extended(node: Node) -> list[str]:
    """Names in an interface's `extends` list, without type arguments."""
    names: list[str] = []
    for c in node.children:
        if c.type == "extends_interfaces":
            for t in c.named_children:
                for n in (t.named_children if t.type == "type_list" else [t]):
                    names.append(re.sub(r"<.*", "", re.sub(r"\s+", "", _t(n)), flags=re.S))
    return names


def _inherit(out: dict, ifaces: dict, declared: dict, parents: dict) -> dict[str, set[str]]:
    """MyBatis lets a mapper interface extend another: the parent's statements are the child's too. Copies each
    ancestor's sinks onto a child that does not redeclare that method (same name and parameter count). Returns the
    inherited method names per interface, so the safe ones are known as that mapper's methods as well."""
    known = set(ifaces)
    simple: dict[str, set[str]] = {}
    for fqn in known:
        simple.setdefault(fqn.rsplit(".", 1)[-1], set()).add(fqn)

    def resolve(raw: str, fqn: str) -> set[str]:
        _, jf, cls = parents[fqn]
        if raw in known:
            return {raw}
        cands = simple.get(raw.rsplit(".", 1)[-1], set())
        if not cands:
            return set()
        outer = cls.outer
        if outer is not None and "." not in raw:  # a member interface of the enclosing type
            nested = f"{_fqn(outer, _scope(jf)[0])}.{raw}"
            if nested in cands:
                return {nested}
        return _resolve_type(raw, cands, jf)

    direct = {fqn: set().union(*(resolve(r, fqn) for r in raws), set()) - {fqn} for fqn, (raws, _, _) in parents.items()}

    def ancestors(fqn: str) -> list[str]:  # nearest first, each once, cycles cut
        seen: list[str] = []
        queue = list(direct.get(fqn, ()))
        while queue:
            a = queue.pop(0)
            if a in seen or a == fqn:
                continue
            seen.append(a)
            queue.extend(sorted(direct.get(a, ())))
        return seen

    inherited: dict[str, set[str]] = {}
    snapshot = {k: dict(v) for k, v in out.items()}
    for fqn in parents:
        own = set(declared.get(fqn, set()))  # grows with each nearer ancestor: a redeclaration there hides farther ones
        for a in ancestors(fqn):
            for nm in ifaces.get(a, {}):
                inherited.setdefault(fqn, set()).add(nm)
            for nm, pos in snapshot.get(a, {}).items():
                if nm in out.get(fqn, {}):
                    continue
                # the parent's parameter count, to tell an override from an overload (unknown count: assume inherited)
                m = ifaces[a].get(nm) if a in ifaces else None
                count = len(_params(m)) if m is not None else None
                if count is not None and (nm, count) in own:
                    continue
                out.setdefault(fqn, {})[nm] = pos
            own |= declared.get(a, set())
    return inherited


def _fqn(cls, pkg: str) -> str:
    parts = []
    while cls is not None:
        parts.append(cls.name)
        cls = cls.outer
    return ".".join(([pkg] if pkg else []) + parts[::-1])


_SCOPES: dict = {}


def _scope(jf) -> tuple[str, dict[str, str], list[str]]:
    """(package, single-type imports by simple name, on-demand import packages) of a Java file."""
    key = getattr(jf, "path", None)
    if key in _SCOPES and _SCOPES[key][0] is jf:
        return _SCOPES[key][1]
    pkg, single, wild = "", {}, []
    for child in jf.root.children:
        text = _t(child).strip().rstrip(";").strip()
        if child.type == "package_declaration":
            pkg = re.sub(r"\s+", "", text[len("package"):])
        elif child.type == "import_declaration" and not re.match(r"import\s+static\b", text):
            name = re.sub(r"\s+", "", text[len("import"):])
            if name.endswith(".*"):
                wild.append(name[:-2])
            else:
                single[name.rsplit(".", 1)[-1]] = name
    _SCOPES[key] = (jf, (pkg, single, wild))
    return pkg, single, wild


def _resolve_type(recv_type: str, cands: set[str], jf) -> set[str]:
    """Which mapper interfaces a receiver type can be, by Java's own rules: a single-type import, then the caller's
    package, then on-demand imports. When the caller does not settle it, every candidate (unknown means keep)."""
    if "." in recv_type:  # written qualified
        return {recv_type} & cands or cands
    if jf is None or len(cands) == 1 and next(iter(cands)) == recv_type:
        return cands
    pkg, single, wild = _scope(jf)
    if recv_type in single:
        return {single[recv_type]} & cands  # imported from elsewhere: not one of these mappers
    local = f"{pkg}.{recv_type}" if pkg else recv_type
    if local in cands:
        return {local}
    on_demand = {f"{w}.{recv_type}" for w in wild} & cands
    return on_demand or cands


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


def positions(table: dict[str, dict[str, list[int]]], name: str, recv_type: str | None, recv_text: str,
              caller=None) -> list[int] | None:
    """Sink positions for a call `recv.name(...)` on a mapper, or None. `caller` is the calling JavaFile: two
    mappers with one simple name in different packages are told apart by its package and imports."""
    every = table.get("*methods*", {})
    if recv_type:
        recv_type = getattr(recv_type, "full", recv_type)  # the spelling it was declared with, when qualified
        simple = recv_type.rsplit(".", 1)[-1]
        cands = table.get("*simple*", {}).get(simple, set())
        hit: set[int] = set()
        found = False
        for fqn in _resolve_type(recv_type, cands, caller) if cands else ():
            if fqn in table and name in table[fqn]:
                hit.update(table[fqn][name])
                found = True
        return sorted(hit) if found else None
    owners = [(i, m[name]) for i, m in table.items() if not i.startswith("*") and name in m]
    low = recv_text.lower()
    # the receiver's type is unknown: only a method name that no other mapper declares, on a mapper-looking receiver
    if len(owners) == 1 and len(every.get(name, {owners[0][0]})) == 1 and any(h in low for h in ("mapper", "dao", "repository", "repo")):
        return owners[0][1]
    return None
