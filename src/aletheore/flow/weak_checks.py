"""Weak validation checks that taint tracking cannot see because no request value is involved at the check itself.

Path containment by string prefix: `extract_path.start_with?(dest_dir)` accepts `/srv/upload-evil/x` for a root of `/srv/upload`, because the
prefix ends without a path separator. The check is only meaningful on a canonical path, so a nearby canonicalising call is required; a prefix
that visibly ends in a separator, or is joined with one, is fine. Found by reading the code around the check, not by following a value."""
from __future__ import annotations

import re
from pathlib import Path

from aletheore.flow import library as _lib
from aletheore.flow import redos as _redos
from aletheore.scanner.detect import IGNORED_DIRS

_EXTS = {".rb": "ruby", ".py": "python", ".go": "go", ".java": "java", ".kt": "kotlin", ".js": "javascript", ".ts": "javascript", ".mjs": "javascript",
         ".cs": "csharp", ".php": "php"}
_PATTERNS = {
    "ruby": re.compile(r"(?P<a>[\w:@.\[\]()'\"]+?)\.start_with\?\(\s*(?P<b>[^)]+?)\s*\)"),
    "python": re.compile(r"(?P<a>[\w.\[\]()'\"]+?)\.startswith\(\s*(?P<b>[^),]+?)\s*\)"),
    "go": re.compile(r"strings\.HasPrefix\(\s*(?P<a>[^,]+?)\s*,\s*(?P<b>[^)]+?)\s*\)"),
    "java": re.compile(r"(?P<a>[\w.\[\]()\"]+?)\.startsWith\(\s*(?P<b>[^)]+?)\s*\)"),
    "kotlin": re.compile(r"(?P<a>[\w.\[\]()\"?]+?)\.startsWith\(\s*(?P<b>[^)]+?)\s*\)"),
    "javascript": re.compile(r"(?P<a>[\w.\[\]()'\"]+?)\.startsWith\(\s*(?P<b>[^)]+?)\s*\)"),
    "csharp": re.compile(r"(?P<a>[\w.\[\]()\"]+?)\.StartsWith\(\s*(?P<b>[^),]+?)\s*[,)]"),
    "php": re.compile(r"(?:str_starts_with|strpos)\(\s*(?P<a>[^,]+?)\s*,\s*(?P<b>[^,)]+?)\s*\)"),
}
_A_NAME = re.compile(r"(path|file|entry|dest|target|extract|resolved|canonical|full|abs|real|child|candidate|requested)", re.I)
_B_NAME = re.compile(r"(dir|root|base|dest|folder|target|home|upload|storage|directory|parent)", re.I)
_SEP = re.compile(r"""(\bsep\b|[Ss]eparator|SEPARATOR|["'][/\\]["']|\+\s*["'][/\\]|["'][/\\]\s*\)?\s*$|\bjoin\b|\bJoin\b|\bCombine\b|\+\s*File\.|\+\s*os\.|/\s*["']\s*$)""")
_CANON = re.compile(r"(realpath|abspath|expand_path|absolute_path|Clean\(|filepath\.Abs|getCanonical|toRealPath|normalize|GetFullPath|\.resolve\(|cleanpath|EvalSymlinks|path\.resolve|toAbsolutePath)")
_GUARD = re.compile(r"(\bunless\b|\bif\b|\bnot\b|!|raise|throw|return false|\bassert|\breject\b|\babort\b|\bdie\b)")
_REJECT = re.compile(r"(\braise\b|\bthrow\b|\breturn\b|\babort\b|\bdie\b|\bfail\b|\bpanic\b|\bexit\b|\bErrorf?\(|\berror\(|\bassert|\bnew \w*(Exception|Error)\b|\bhalt\b|\brender\b.*(40\d|forbidden)|\bhead\b.*40\d)")
_LITERAL = re.compile(r"""^\s*(["']).*\1\s*$""")
_SKIP = set(IGNORED_DIRS) | {".worktrees", ".repowise", "site-packages", "migrations"} | set(_lib.SKIP_DIRS) | {"tasks", "rakelib", "script", "bin", "build-logic", "buildSrc"}
MAX_FILES = 6000

# Archive extraction: an entry name from the archive is joined to the destination and written without any containment check in the file
# ("Zip Slip": an entry named ../../etc/x leaves the directory). The file must use an archive library, join an entry name into a path, and contain
# no containment guard anywhere; a guard anywhere in the file is enough to stay quiet, which keeps this to the plainly unguarded extractors.
_ARCHIVE = {
    "java": (re.compile(r"ZipEntry|ZipInputStream|ZipFile|ArchiveEntry|TarEntry|TarInputStream|TarArchive"),
             re.compile(r"new\s+File\(([^;]*?,\s*)?[\w.()]*?(entry|e|ze|zipEntry|tarEntry|item)\w*\.getName\(\)|(Paths\.get|resolve)\([^;]*(entry|ze|zipEntry|tarEntry|item)\w*\.getName\(\)|File\([^;]*(entry|ze)\w*\.getName\(\)", re.I),
             re.compile(r"getCanonical|normalize\(|startsWith|toRealPath|isInside|\.\.|ZipSlip|zip.?slip|isSubPath|SecureJoin|sanitiz|validat", re.I)),
    "kotlin": (re.compile(r"ZipEntry|ZipInputStream|ZipFile|ArchiveEntry|TarEntry|TarArchive"),
               re.compile(r"File\([^\n]*(entry|ze|zipEntry|tarEntry|item)\w*\.(name|getName\(\))|resolve\([^\n]*(entry|ze|item)\w*\.name", re.I),
               re.compile(r"canonical|normalize\(|startsWith|toRealPath|\.\.|zip.?slip|sanitiz|validat", re.I)),
    "python": (re.compile(r"\btarfile\b|\bzipfile\b"),
               re.compile(r"os\.path\.join\([^)]*\b(member|entry|info|item|zinfo|tarinfo|zi|ti|m)\w*\.(name|filename)\b"),
               re.compile(r"realpath|abspath|commonpath|is_relative_to|relative_to|filter\s*=|data_filter|tar_filter|startswith|\.\.|safe|sanitiz|is_within|secure_filename", re.I)),
    "go": (re.compile(r"zip\.(NewReader|OpenReader)|tar\.NewReader|archiver\.|\.Extract\("),
           re.compile(r"filepath\.Join\([^)]*\b(f|file|hdr|header|entry|zf)\w*\.Name\b"),
           re.compile(r"HasPrefix|filepath\.Rel\b|IsLocal|SecureJoin|ZipSlip|Contains\([^)]*\"\.\.\"|sanitiz|ValidPath|validat|illegal file path", re.I)),
    "javascript": (re.compile(r"unzipper|yauzl|adm-zip|AdmZip|tar-stream|node-stream-zip|extract-zip|decompress|jszip|\"tar\""),
                   re.compile(r"path\.(join|resolve)\([^)]*\b(entry|header|zipEntry)\w*\.(path|fileName|entryName|name)\b"),
                   re.compile(r"startsWith|path\.relative|\.\.|isInside|sanitiz|zip.?slip|normalize|realpath|validat", re.I)),
    "csharp": (re.compile(r"ZipArchive|ZipFile|ZipEntry|TarEntry|TarReader"),
               re.compile(r"Path\.Combine\([^;]*\b(entry|zipEntry|archiveEntry|zae|ze)\w*\.(FullName|Name)\b"),
               re.compile(r"GetFullPath|StartsWith|IsPathRooted|\.\.|zip.?slip|sanitiz|IsSubPath|ValidatePath", re.I)),
    "ruby": (re.compile(r"Zip::File|Zip::InputStream|TarReader|Minitar"),
             re.compile(r"File\.join\([^)]*\b(entry|f|file|e)\w*\.(name|full_name)\b"),
             re.compile(r"start_with|expand_path|\.\.|realpath|sanitiz|safe|within|inside", re.I)),
}


_WRITES = re.compile(r"(FileOutputStream|Files\.(copy|write|newOutputStream|createDirectories)|mkdirs?\b|createNewFile|os\.(Create|OpenFile|MkdirAll)|WriteFile|(?-i:\bopen\()|createWriteStream|writeFile|mkdir|File\.Create|FileStream|CreateDirectory|ExtractToFile|File\.(open|write|binwrite)|FileUtils|copy_stream|mkdir_p|\.extract\(|extractall|copyTo|CopyTo|outputStream|\.write\()", re.I)


def _archive(lang: str, text: str, lines: list[str], rel: str) -> list[dict]:
    marker, join, guard = _ARCHIVE[lang]
    if not marker.search(text) or guard.search(text):
        return []
    tar_extract = re.compile(r"\b(tar|tf|archive|t|tarball|tar_file|tar_obj|tfile)\w*\.extractall\((?![^)]*(filter\s*=|members\s*=))[^\n]*\)") if lang == "python" and "tarfile" in text else None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(("#", "//", "*", "/*")):
            continue
        if (join.search(line) or (tar_extract is not None and tar_extract.search(line))) and _WRITES.search("\n".join(lines[max(0, i - 8): i + 12])):
            return [{"tool": "aletheore-flow", "rule_id": f"flow.{lang}.archive-path", "severity": "minor", "type": "vulnerability", "path": rel, "line": i + 1,
                     "cwe": ["CWE-22"], "message": "An archive entry name is joined into an output path and nothing in this file checks that the result stays inside "
                                                  "the destination, so an entry named ../../x writes outside it (Zip Slip). Resolve the path and check it starts with "
                                                  "the destination plus a separator."}]
    return []


def _balanced(line: str, open_at: int) -> str:
    """The text between the parenthesis at open_at and its match (the whole argument list of the call)."""
    depth = 0
    for j in range(open_at, len(line)):
        if line[j] == "(":
            depth += 1
        elif line[j] == ")":
            depth -= 1
            if depth == 0:
                return line[open_at + 1: j]
    return line[open_at + 1:]


def _last_ident(text: str) -> str:
    ids = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
    return ids[-1] if ids else ""


_ENDS = {
    "ruby": re.compile(r"(?P<a>[\w:@.\[\]()'\"]+?)\.end_with\?\(\s*(?P<b>\"[^\"]+\"|'[^']+')\s*\)"),
    "python": re.compile(r"(?P<a>[\w.\[\]()'\"]+?)\.endswith\(\s*(?P<b>\"[^\"]+\"|'[^']+')\s*\)"),
    "go": re.compile(r"strings\.HasSuffix\(\s*(?P<a>[^,]+?)\s*,\s*(?P<b>\"[^\"]+\")\s*\)"),
    "java": re.compile(r"(?P<a>[\w.\[\]()\"]+?)\.endsWith\(\s*(?P<b>\"[^\"]+\")\s*\)"),
    "kotlin": re.compile(r"(?P<a>[\w.\[\]()\"?]+?)\.endsWith\(\s*(?P<b>\"[^\"]+\")"),
    "javascript": re.compile(r"(?P<a>[\w.\[\]()'\"]+?)\.endsWith\(\s*(?P<b>\"[^\"]+\"|'[^']+')\s*\)"),
    "csharp": re.compile(r"(?P<a>[\w.\[\]()\"]+?)\.EndsWith\(\s*(?P<b>\"[^\"]+\")"),
    "php": re.compile(r"str_ends_with\(\s*(?P<a>[^,]+?)\s*,\s*(?P<b>'[^']+'|\"[^\"]+\")\s*\)"),
}
_HOST_A = re.compile(r"(host|hostname|netloc|domain|origin|referer|referrer|authority|server_name)", re.I)
_DOMAIN_LIT = re.compile(r"""^["']([a-z0-9-]+\.)+[a-z]{2,}["']$""", re.I)
_URL_A = re.compile(r"(^|\W)(next|redirect|return_?url|returnto|return_?to|continue|goto|after_?login|success_?url|back_?url|redirect_?to)\w*\s*$", re.I)
_REDIRECTS = re.compile(r"(redirect|Redirect|Location|location\.href|sendRedirect|HttpResponseRedirect|http\.Redirect)")
_SLASH_LIT = re.compile(r"""^\s*["']/["']\s*$""")
_REDIRECT_SAFE = re.compile(r"""(["']//["']|\\\\|urlparse|urlsplit|netloc|is_safe|allowed_host|\bUri\b|new URL|URL\(|\.host\b|hostname|isLocalUrl|IsLocalUrl|LocalRedirect|Url\.IsLocal|\.scheme)""")


def scan(repo: Path) -> list[dict]:
    out: list[dict] = []
    repo_r = repo.resolve()
    n = 0
    for p in repo.rglob("*"):
        lang = _EXTS.get(p.suffix)
        if lang is None or not p.is_file():
            continue
        try:
            rel_parts = p.relative_to(repo).parts
        except ValueError:
            continue
        if _SKIP.intersection(rel_parts[:-1]) or p.name.endswith((".min.js", ".d.ts")) or ".test." in p.name or ".spec." in p.name or p.name.endswith(("_test.go", "Test.java")):
            continue
        n += 1
        if n > MAX_FILES:
            break
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if len(raw) <= 600_000:
            text0 = raw.decode("utf-8", "replace")
            lines0 = text0.splitlines()
            rel0 = str(p.relative_to(repo_r)) if p.is_relative_to(repo_r) else str(p)
            if lang in _ARCHIVE:
                out.extend(_archive(lang, text0, lines0, rel0))
            out.extend(_redos.scan_lines(lang, lines0, rel0))
        if len(raw) > 600_000 or (b"start_with?" not in raw and b"startswith" not in raw and b"HasPrefix" not in raw and b"startsWith" not in raw
                                  and b"StartsWith" not in raw and b"strpos" not in raw and b"str_starts_with" not in raw
                                  and b"end_with?" not in raw and b"endswith" not in raw and b"HasSuffix" not in raw and b"endsWith" not in raw
                                  and b"EndsWith" not in raw and b"str_ends_with" not in raw):
            continue
        lines = raw.decode("utf-8", "replace").splitlines()
        pat = _PATTERNS[lang]
        ends = _ENDS[lang]
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*", "/*")):
                continue
            for m in ends.finditer(line):  # host.endswith("example.com") accepts evilexample.com
                a, b = m.group("a"), m.group("b")
                if _HOST_A.search(_last_ident(a) or a) and _DOMAIN_LIT.match(b) and not b[1:].startswith(".") and _GUARD.search(line) \
                        and _REJECT.search("\n".join(lines[i: i + 4]) + (line if "&&" in line or " and " in line else "")):
                    out.append({"tool": "aletheore-flow", "rule_id": f"flow.{lang}.host-suffix-check", "severity": "minor", "type": "vulnerability",
                                "path": str(p.relative_to(repo_r)) if p.is_relative_to(repo_r) else str(p), "line": i + 1, "cwe": ["CWE-601"],
                                "message": "A host is validated by suffix without a leading dot, so evilexample.com passes a check for example.com. "
                                           "Compare the whole host or require a dot before the suffix."})
                    break
            sl = re.search(r"(?P<a>[\w.\[\]()'\"]+?)\.(startswith|startsWith|start_with\?|StartsWith)\(\s*(?P<b>\"/\"|'/')\s*\)", line) or \
                re.search(r"strings\.HasPrefix\(\s*(?P<a>[^,]+?)\s*,\s*(?P<b>\"/\")\s*\)", line)
            if sl and _URL_A.search(sl.group("a")) and _GUARD.search(line) and _REJECT.search("\n".join(lines[i: i + 4])):
                near = "\n".join(lines[max(0, i - 6): i + 6])
                if not _REDIRECT_SAFE.search(near) and _REDIRECTS.search("\n".join(lines[max(0, i - 10): i + 12])):
                    out.append({"tool": "aletheore-flow", "rule_id": f"flow.{lang}.redirect-prefix-check", "severity": "minor", "type": "vulnerability",
                                "path": str(p.relative_to(repo_r)) if p.is_relative_to(repo_r) else str(p), "line": i + 1, "cwe": ["CWE-601"],
                                "message": "A redirect target is validated by checking that it starts with /, which accepts //evil.com (a protocol-relative URL) "
                                           "and /\\evil.com. Parse the URL and compare its host, or reject a leading // and backslash."})
            for m in pat.finditer(line):
                a, b = m.group("a"), m.group("b")
                call_open = line.find("(", m.start("b") - 1 - (len(m.group(0)) - len(m.group(0).lstrip())))
                lp = line.rfind("(", 0, m.start("b") + 1)
                if lp != -1 and lang in ("ruby", "python", "java", "kotlin", "javascript", "csharp"):
                    full = _balanced(line, lp)
                    b = full.split(",")[0].strip() if lang in ("python", "csharp") and "(" not in full.split(",")[0] else full.strip()
                if not _A_NAME.search(_last_ident(a) or a) or _LITERAL.match(b) or not _B_NAME.search(b):
                    continue
                if lang == "php" and "strpos" in m.group(0) and not re.search(r"strpos\([^;]*\)\s*(===|==|!==|!=)\s*0", line):
                    continue
                if _SEP.search(b) or not _GUARD.search(line):
                    continue
                if lang in ("java", "kotlin"):
                    text_all = raw.decode("utf-8", "replace")
                    ids = {_last_ident(a), _last_ident(b)} - {""}
                    if any(re.search(rf"\bPath\s+{re.escape(x)}\b|\b{re.escape(x)}\s*:\s*Path\b|\b{re.escape(x)}\s*=\s*(Paths?\.(get|of)|Path\.of)", text_all) for x in ids):
                        continue  # a java.nio.file.Path: startsWith compares whole components
                if lang in ("java", "kotlin", "csharp") and re.search(r"\bPath\b|Paths?\.|\brelativize\b|\.toPath\(", a + " " + b) \
                        and not re.search(r"getCanonicalPath|getAbsolutePath|GetFullPath|toString\(\)", a):
                    continue  # java.nio Path.startsWith compares whole components
                if not _REJECT.search("\n".join(lines[i: i + 4])):
                    continue  # the check does not reject anything: it is not a containment guard
                window = "\n".join(lines[max(0, i - 25): i + 1])
                if not _CANON.search(window):
                    continue
                bid = _last_ident(b)
                if bid and re.search(rf"\b{re.escape(bid)}\b\s*(=|:=|<<)[^\n]*(sep|SEPARATOR|Separator|[\"'][/\\][\"']|\bjoin\b|\bJoin\b|\bCombine\b)", window):
                    continue  # the prefix variable was built with a trailing separator
                out.append({
                    "tool": "aletheore-flow", "rule_id": f"flow.{lang}.path-containment", "severity": "minor", "type": "vulnerability",
                    "path": str(p.relative_to(repo_r)) if p.is_relative_to(repo_r) else str(p), "line": i + 1, "cwe": ["CWE-22"],
                    "message": "A path is checked to be inside a directory by string prefix, and the prefix does not end in a path separator, so a sibling such "
                               "as /srv/upload-evil passes a check against /srv/upload. Compare whole path components or add the separator.",
                })
                break
    return out
