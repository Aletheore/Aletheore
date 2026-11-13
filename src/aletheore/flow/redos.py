"""Regular expressions that backtrack catastrophically, found by reading the pattern (no request value involved).

Only the exponential case is reported (a trailing-whitespace pattern such as `\\s+$` is far too common to be worth a finding). Exponential: a group repeated without bound whose body can start and end with something repeated without bound, so one string splits many ways
(`(a+)+`, `(\\s*\\w+\\s*)*`, `(\\d+,?)+`). Go and Rust use
linear-time engines and are not scanned."""
from __future__ import annotations

import re

_UNB = {"+", "*"}


def _quant(p: str, i: int) -> tuple[str, int]:
    if i < len(p) and p[i] in "+*?":
        q = p[i]; i += 1
    elif i < len(p) and p[i] == "{":
        m = re.match(r"\{(\d+)(,(\d*))?\}", p[i:])
        if not m:
            return "", i
        q = "*" if m.group(2) and m.group(3) == "" else "{}"
        i += m.end()
    else:
        return "", i
    if i < len(p) and p[i] == "?" and q in ("+", "*"):
        i += 1  # lazy: still repeats
    elif i < len(p) and p[i] == "+" and q in ("+", "*"):
        return "p", i + 1  # possessive: never backtracks into the repeat
    return q, i


def _parse(p: str, i: int = 0, depth: int = 0):
    """Returns (alternatives, next index); an alternative is a list of (kind, body, quant)."""
    alts: list[list] = [[]]
    while i < len(p):
        c = p[i]
        if c == ")":
            return alts, i
        if c == "|":
            alts.append([]); i += 1; continue
        if c == "(":
            j = i + 1
            if p.startswith("?", j):
                m = re.match(r"\?(?:[:=!]|<[=!]|<\w+>|P<\w+>)", p[j:])
                j += m.end() if m else 1
            if depth > 12:
                return alts, len(p)
            body, k = _parse(p, j, depth + 1)
            q, k2 = _quant(p, k + 1)
            alts[-1].append(("group", body, q)); i = k2; continue
        if c == "[":
            j = i + 1
            if j < len(p) and p[j] == "^":
                j += 1
            if j < len(p) and p[j] == "]":
                j += 1
            while j < len(p) and p[j] != "]":
                j += 2 if p[j] == "\\" else 1
            q, k = _quant(p, j + 1)
            alts[-1].append(("atom", p[i: j + 1], q)); i = k; continue
        if c == "\\":
            q, k = _quant(p, i + 2)
            alts[-1].append(("atom", p[i: i + 2], q)); i = k; continue
        if c in "^$":
            alts[-1].append(("anchor", c, "")); i += 1; continue
        q, k = _quant(p, i + 1)
        alts[-1].append(("atom", c, q)); i = k
    return alts, i


def _chars(item) -> set[str] | None:
    """The ASCII characters a single atom can match, or None when it is a group (unknown)."""
    if item[0] != "atom":
        return None
    try:
        rx = re.compile(item[1])
    except re.error:
        return None
    return {chr(c) for c in range(128) if rx.fullmatch(chr(c))}


def _overlap(first, last) -> bool:
    a, b = _chars(first), _chars(last)
    return a is None or b is None or bool(a & b)


def _ambiguous(body) -> bool:
    for alt in body:
        if not alt:
            continue
        first, last = alt[0], alt[-1]
        if len(alt) == 1 and first[2] in _UNB:
            return True
        if first[2] in _UNB and ((last[2] in _UNB and _overlap(first, last)) or (last[2] == "?" and last is not first)):
            return True
        if len(alt) == 1 and first[0] == "group" and _ambiguous(first[1]):
            return True
        if len(alt) > 1 and last[0] == "atom" and last[2] in _UNB:
            # the body ends in a repeat the next iteration can start with (optional leading items are skipped)
            for it in alt[:-1]:
                if it[0] != "atom":
                    break
                if it[2] in ("?", "*"):
                    if it[2] == "*" and _overlap(it, last):
                        return True
                    continue
                if _overlap(it, last):
                    return True
                break
            else:
                return True
        if len(alt) > 1 and last[0] == "atom" and last[2] == "" and first[0] == "atom" and first[2] in _UNB:
            cf, cl = _chars(first), _chars(last)
            if cf is not None and cl is not None and cl <= cf:
                return True  # `(.*,)+`: the separator is itself matched by the repeat before it
    return False


def _adjacent(alts) -> bool:
    """Two unbounded repeats side by side over the same characters, with only characters from that set between them (`\\s*\\n\\s*`, `\\S+@\\S+`): quadratic."""
    for alt in alts:
        body = [x for x in alt if x[0] != "anchor"]
        for i, x in enumerate(body):
            if x[0] != "atom" or x[2] not in _UNB or x[1] == ".":
                continue
            cx = _chars(x)
            if cx is None:
                continue
            for y in body[i + 1:]:
                if y[0] != "atom":
                    break
                cy = _chars(y)
                if cy is None:
                    break
                if y[2] in _UNB and cx & cy and y[1] != ".":
                    return True
                if y[2] == "" and cy <= cx:
                    continue
                break
        for x in alt:
            if x[0] == "group" and _adjacent(x[1]):
                return True
    return False


def _walk(alts) -> bool:
    for alt in alts:
        for kind, body, q in alt:
            if kind == "group":
                if q in _UNB and _ambiguous(body):
                    return True
                if _walk(body):
                    return True
    return False


_WS = re.compile(r"^(\\s|\[\\s\]|\[ \\t\]|\[\\t \]| |\[ \])$")


def weakness(pattern: str) -> str | None:
    """'exponential', 'polynomial' or None."""
    if len(pattern) > 400 or "(?<" in pattern and "(?<=" not in pattern and "(?<!" not in pattern and False:
        return None
    try:
        alts, _ = _parse(pattern)
    except RecursionError:
        return None
    if _walk(alts):
        return "exponential"
    if _adjacent(alts):
        return "polynomial"
    return None


_JS_LIT = re.compile(r"""(?:(?<=[=(:,!&|?{};\[])|(?<=return )|^)\s*/((?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[*])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])*)/([gimsuyd]*)(?=[.,;)\s\]}]|$)""")
_STR_CALL = {
    "javascript": re.compile(r"""new\s+RegExp\(\s*(?P<q>["'`])(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)"""),
    "python": re.compile(r"""\bre\.(?:compile|match|search|sub|subn|findall|finditer|split|fullmatch)\(\s*[rRbB]*(?P<q>["'])(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)"""),
    "java": re.compile(r"""Pattern\.(?:compile|matches)\(\s*(?P<q>")(?P<s>(?:\\.|[^"\\])*)"|\.(?:matches|replaceAll|replaceFirst|split)\(\s*(?P<q2>")(?P<s2>(?:\\.|[^"\\])*)\""""),
    "csharp": re.compile(r"""new\s+(?:[\w.]+\.)?Regex\(\s*@?(?P<q>")(?P<s>(?:\\.|[^"\\])*)"|Regex\.(?:IsMatch|Match|Matches|Replace|Split)\([^,]+,\s*@?(?P<q2>")(?P<s2>(?:\\.|[^"\\])*)\""""),
    "php": re.compile(r"""preg_\w+\(\s*(?P<q>["'])/(?P<s>(?:\\.|(?!(?P=q)).)*?)/[a-z]*(?P=q)"""),
    "kotlin": re.compile(r"""Regex\(\s*(?P<q>")(?P<s>(?:\\.|[^"\\])*)\""""),
    "ruby": re.compile(r"""(?:^|[=(,\s!~])/((?:\\.|[^/\\\n])+)/[imxounse]*(?=[.,;)\s]|$)"""),
}


def patterns(lang: str, line: str):
    if lang == "javascript":
        for m in _JS_LIT.finditer(line):
            yield m.group(1)
    if lang == "ruby":
        for m in _STR_CALL["ruby"].finditer(line):
            yield m.group(1)
        return
    rx = _STR_CALL.get(lang)
    if rx is None:
        return
    for m in rx.finditer(line):
        s = m.groupdict().get("s") or m.groupdict().get("s2")
        if not s:
            continue
        if lang in ("javascript", "java", "kotlin", "csharp", "python") and not (lang == "python" and "r" in line[max(0, m.start()): m.start("q") + 1].lower().split("(")[-1]):
            s = s.replace("\\\\", "\\")
        yield s


_VENDORED = {"vendor", "vendors", "plugins", "third_party", "thirdparty", "static", "public", "dist", "bower_components", "assets", "libs", "min"}


def scan_lines(lang: str, lines: list[str], rel: str) -> list[dict]:
    if (lang not in _STR_CALL and lang != "javascript") or _VENDORED.intersection(rel.split("/")[:-1]):
        return []
    found: list[dict] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(("#", "//", "*", "/*")) or len(line) > 600:
            continue
        for pat in patterns(lang, line):
            kind = weakness(pat)
            if kind:
                found.append({"tool": "aletheore-flow", "rule_id": f"flow.{lang}.redos", "severity": "minor", "type": "vulnerability", "path": rel, "line": i + 1,
                              "cwe": ["CWE-1333", "CWE-730"],
                              "message": ("A regular expression repeats a group whose body can match the same text in several ways, so a crafted string makes it backtrack exponentially (ReDoS). "
                                          if kind == "exponential" else
                                          "A regular expression has two adjacent repeats over overlapping characters, so a long run of them that then fails to match is retried from every split point (quadratic ReDoS). ")
                              + "Rewrite it without nested repetition or bound the input length."})
                break
        if len(found) >= 3:
            break
    return found
