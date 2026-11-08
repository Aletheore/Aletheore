"""Shared pieces of condition-guard tracking: what a validating `if` proves about a variable.

A guard fact is a pair (when_true, when_false) of sets of names proven safe on that outcome. A name `x#..` means
"x is known not to contain `..`"; path traversal is excluded only when `..`, `/` and `\\` are all excluded.
"""

from __future__ import annotations

import re

Facts = tuple[set, set]
EMPTY: Facts = (set(), set())
PATH_TOKENS = ("..", "/", "\\")


def negate(f: Facts) -> Facts:
    return (f[1], f[0])


def both(a: Facts, b: Facts) -> Facts:
    """Facts for `a && b`: true means both true; false means at least one false."""
    return (a[0] | b[0], a[1] & b[1])


def either(a: Facts, b: Facts) -> Facts:
    """Facts for `a || b`: true means at least one true; false means both false."""
    return (a[0] & b[0], a[1] | b[1])


_ALLOWED_CLASS = ("a-z", "A-Z", "0-9", "\\w", "\\d", "_", "-", " ", "$", "\\-", "\\_")


def safe_regex(pattern: str, cwes: set[int] | None = None, implicit_anchor: bool = False) -> bool:
    """An anchored pattern whose every match is harmless text: only fixed characters and simple character classes,
    each with an optional quantifier, optionally grouped into alternatives of fixed text. Slashes, dots outside a
    class that excludes traversal, quotes, spaces in SQL and so on are not allowed."""
    p = pattern
    if implicit_anchor and not p.startswith("^") and not p.startswith("/") and not p.startswith("\\A"):
        p = "^" + p + "$"  # String.matches / Matcher.matches must match the whole input
    if p.startswith("/") and re.search(r"/[a-zA-Z]*$", p):
        p = p[1:p.rindex("/")]
    if not (p.startswith("^") and p.endswith("$")) and not (p.startswith("\\A") and (p.endswith("\\z") or p.endswith("\\Z"))):
        return False
    p = p[1:-1] if p.startswith("^") else p[2:-2]
    allow_dot = 22 not in (cwes or set())
    allowed = list(_ALLOWED_CLASS) + (["."] if allow_dot else [])
    quant = r"(?:[+*?]|\{\d+(?:,\d*)?\})?"
    atom_fixed = r"[A-Za-z0-9_\-]"
    token = rf"(?:\\[dw]|\[[^\]]+\]|{atom_fixed}|\([A-Za-z0-9_\-|]+\)){quant}"
    if not re.fullmatch(rf"(?:{token})+", p):
        return False
    for cls in re.findall(r"\[([^\]]+)\]", p):
        rest = cls
        for tok in allowed:
            rest = rest.replace(tok, "")
        if rest != "" or cls.startswith("^"):
            return False
    return True


def resolve(names: set, cwes: set[int]) -> set[str]:
    """Turn raw fact names into the variables to mark safe."""
    out: set[str] = set()
    tokens: dict[str, set[str]] = {}
    for n in names:
        if n.endswith("@canon"):
            if 22 in cwes:
                out.add(n[:-6])
            continue
        if n.endswith("@918"):
            if 918 in cwes:
                out.add(n[:-4])
            continue
        if "#" in n:
            var, tok = n.split("#", 1)
            tokens.setdefault(var, set()).add(tok)
        else:
            out.add(n)
    if 22 in cwes:
        for var, toks in tokens.items():
            if all(t in toks for t in PATH_TOKENS):
                out.add(var)
    return out


HOST_ATTRS = {"host", "hostname", "netloc", "authority", "Host", "Hostname", "getHost", "getAuthority", "host_str", "HostName", "Authority", "origin"}
CANON_CALLS = r"(GetFullPath|getCanonicalPath|getCanonicalFile|toRealPath|normalize|filepath\.Clean|filepath\.Abs|EvalSymlinks|realpath|abspath|expand_path|canonicalize|Canonicalize)"
STARTS = {"StartsWith", "startsWith", "HasPrefix", "start_with?", "starts_with", "startswith"}


def canonical_assignment(src: bytes, var: str) -> bool:
    """True when the variable is assigned from a canonicalising call somewhere in the file (`target = Path.GetFullPath(...)`)."""
    return re.search(rb"\b" + re.escape(var.encode()) + rb"\b\s*(:=|=)[^;\n]*" + CANON_CALLS.encode(), src) is not None


EQUALS = {"equals", "Equals", "equalsIgnoreCase", "EqualsIgnoreCase", "contentEquals", "EqualFold", "eql?", "equal?", "casecmp?", "isEqualTo"}
MATCHES = {"matches", "IsMatch", "MatchString", "match?", "test", "Match", "fullmatch", "is_match", "matches_x"}
NUMERIC = {"isNumeric", "isDigits", "isCreatable", "isParsable", "isNumber", "is_numeric", "ctype_digit", "isInteger", "IsDigit", "isdigit", "isalnum", "isdecimal", "is_digit_x"}


class GuardEngine:
    """Language-independent reading of a validating condition. A language supplies:

    norm(node)         -> ("not", child) | ("and", l, r) | ("or", l, r) | ("paren", child) | ("eq", l, r) | ("ne", l, r)
                          | ("call", name, recv_node | None, [arg nodes], recv_text) | ("other",)
    local(node)        -> name of a local variable the node is, else None
    const(node)        -> constant string value of the node, else None
    safe(node)         -> True when the node's value is provably harmless (a constant)
    safe_collection(node) -> True when the node is a collection of harmless constants
    pattern_source(node)  -> constant regex text the node stands for (a compiled pattern constant), else None
    """

    def __init__(self, norm, local, const, safe, safe_collection, pattern_source, cwes, anchored=(), attr=None, canon=None):
        self.canon = canon or (lambda var: False)  # canon(var): was the variable produced by a canonicalising call
        self.attr = attr or (lambda n: None)  # attr(node) -> (local variable, host-like attribute) for `u.host`, `new URL(u).hostname`, ...
        self.anchored = set(anchored)  # method names that match the whole string (Kotlin matches, Java matches)
        self.norm, self.local, self.const, self.safe = norm, local, const, safe
        self.safe_collection, self.pattern_source, self.cwes = safe_collection, pattern_source, cwes

    def facts(self, cond) -> Facts:
        if cond is None:
            return EMPTY
        return self._facts_norm(self.norm(cond))

    def _facts_norm(self, n) -> Facts:
        k = n[0]
        if k == "neg_call":  # `!x.matches(..)` that a grammar parsed as `(!x).matches(..)`
            return negate(self._facts_norm(("call",) + tuple(n[1:])))
        if k == "paren" or k == "not":
            inner = self.facts(n[1])
            return inner if k == "paren" else negate(inner)
        if k == "and":
            return both(self.facts(n[1]), self.facts(n[2]))
        if k == "or":
            return either(self.facts(n[1]), self.facts(n[2]))
        if k in ("eq", "ne"):
            for a, b in ((n[1], n[2]), (n[2], n[1])):
                hv = self.attr(a)
                if hv and hv[1] in HOST_ATTRS and b is not None and self.safe(b):
                    return ({hv[0] + "@918"}, set()) if k == "eq" else (set(), {hv[0] + "@918"})
                v = self.local(a)
                if v and b is not None and self.safe(b):
                    return ({v}, set()) if k == "eq" else (set(), {v})
            return EMPTY
        if k != "call":
            return EMPTY
        _, name, recv, args, recv_text = n
        if name in EQUALS:
            ops = ([recv] if recv is not None else []) + list(args)
            if len(ops) == 2:
                for a, b in ((ops[0], ops[1]), (ops[1], ops[0])):
                    hv = self.attr(a)
                    if hv and hv[1] in HOST_ATTRS and b is not None and self.safe(b):
                        return ({hv[0] + "@918"}, set())
                    v = self.local(a)
                    if v and b is not None and self.safe(b):
                        return ({v}, set())
            return EMPTY
        if name in STARTS:
            ops = ([recv] if recv is not None else []) + list(args)
            if len(ops) == 2 and recv_text not in ("strings", "Strings") or len(ops) == 2:
                x, prefix = (args[0], args[1]) if recv_text in ("strings", "bytes") else (ops[0], ops[1])
                v = self.local(x)
                if v and prefix is not None and self.safe(prefix) and self.canon(v):
                    return ({v + "@canon"}, set())  # a canonical path that starts with the fixed root stays inside it
            return EMPTY
        if name in MATCHES:
            target = rx = None
            if recv is not None and len(args) == 1:
                target, rx = self.local(args[0]), self.pattern_source(recv) or self.const(recv)
                if target is None or rx is None:
                    target, rx = self.local(recv), self.const(args[0])  # x.matches("re") / x.match?("re")
            elif recv is None and len(args) == 2 or (recv is not None and len(args) == 2):
                target, rx = self.local(args[1]), self.const(args[0]) or self.pattern_source(args[0])  # Pattern.matches(re, x), Regex.IsMatch(x, re) swapped below
                if target is None:
                    target, rx = self.local(args[0]), self.const(args[1])
            if target and rx is not None and safe_regex(rx, self.cwes, implicit_anchor=name in self.anchored):
                return ({target}, set())
            return EMPTY
        if name in ("Contains", "ContainsAny", "ContainsRune", "contains") and recv_text in ("strings", "bytes", "slices", "Strings") and len(args) == 2:
            v = self.local(args[0])
            tok = self.const(args[1])
            if v and tok is not None:
                chars = {tok} if name in ("Contains", "contains") else set(tok)  # ContainsAny: any one of the characters
                found = {t for t in PATH_TOKENS if t in chars}
                if found:
                    return (set(), {f"{v}#{t}" for t in found})
            return EMPTY
        if name in ("contains", "Contains", "includes", "include?", "Include", "has", "in", "includes?", "contains_key") and len(args) == 1:
            tok = self.const(args[0])
            if tok == "\\\\":
                tok = "\\"  # a raw '\\\\' in source text is one backslash
            v = self.local(recv) if recv is not None else None
            if v and tok is not None and tok in PATH_TOKENS:
                return (set(), {f"{v}#{tok}"})
            v2 = self.local(args[0])
            if v2 and recv is not None and self.safe_collection(recv):
                return ({v2}, set())
            hv = self.attr(args[0])
            if hv and hv[1] in HOST_ATTRS and recv is not None and self.safe_collection(recv):
                return ({hv[0] + "@918"}, set())  # `ALLOWED_HOSTS.include?(u.host)`: the URL points at an allowed host
            return EMPTY
        if name in NUMERIC and (len(args) == 1 or (recv is not None and not args)):
            v = self.local(args[0]) if args else self.local(recv)
            return ({v}, set()) if v else EMPTY
        return EMPTY


def apply(names: set, cwes: set[int], env: dict, safe_value) -> None:
    for v in resolve(names, cwes):
        if v in env:
            env[v] = safe_value
