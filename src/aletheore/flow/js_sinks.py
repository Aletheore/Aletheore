"""Sources, sinks and sanitizers for JavaScript and TypeScript discover mode."""

from __future__ import annotations

import re

SANITIZERS: dict[int, set[str]] = {
    79: {"escape", "escapeHtml", "encode", "sanitize", "sanitizeHtml", "xss", "encodeURIComponent", "encodeURI",
         "escapeHTML", "htmlEscape", "clean", "stripTags", "stripHtml"},
    78: {"quote", "escape", "shellEscape", "shellQuote"},
    89: {"escape", "escapeId", "format"},
    22: {"basename", "sanitize", "sanitizeFilename", "normalize"},
    94: set(),
    601: {"isSafeRedirect", "isLocalUrl", "validateRedirect"},
    918: {"isSafeUrl", "validateUrl"},
    502: set(),
    943: {"escape", "sanitize", "toString", "String"},
    501: set(),
}
SAFE_RESULT = {"parseInt", "parseFloat", "Number", "Boolean", "isNaN", "isFinite", "isInteger", "isArray", "isNumeric",
               "isInt", "isEmail", "isUUID", "length", "includes", "startsWith", "endsWith", "indexOf", "test", "match",
               "hasOwnProperty", "Math"}
REQ_NAMES = ("req", "request")
REQUEST_PROPS = {"query", "body", "params", "cookies", "headers", "url", "originalUrl", "path", "hostname", "host",
                 "signedCookies", "files", "file", "rawBody", "baseUrl", "ip", "protocol", "subdomains", "xhr"}
REQUEST_METHODS = {"get", "header", "param", "accepts", "is", "range"}
KOA_ROOT = {"ctx", "context"}
ROUTE_METHODS = {"get", "post", "put", "delete", "patch", "all", "use", "head", "options", "route", "param"}

FS_ONE = {"readFile", "readFileSync", "writeFile", "writeFileSync", "appendFile", "appendFileSync", "createReadStream",
          "createWriteStream", "unlink", "unlinkSync", "readdir", "readdirSync", "stat", "statSync", "lstat", "lstatSync",
          "rmdir", "rmdirSync", "rm", "rmSync", "mkdir", "mkdirSync", "access", "accessSync", "open", "openSync",
          "chmod", "chmodSync", "exists", "existsSync", "readlink", "realpath", "truncate", "watch", "opendir"}
FS_TWO = {"rename", "renameSync", "copyFile", "copyFileSync", "cp", "cpSync", "symlink", "symlinkSync", "link", "linkSync"}
CHILD_PROC = {"exec", "execSync", "spawn", "spawnSync", "execFile", "execFileSync", "fork"}
HTTP_CLIENT_RECV = {"axios", "got", "request", "needle", "superagent", "http", "https", "fetch", "node-fetch", "urllib", "undici"}
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "request", "fetch"}

DISCOVER_CWES_JS = (89, 78, 22, 79, 94, 601, 918, 502, 943, 501)
CWE_NAMES_JS = {
    89: ("sql-injection", "SQL injection"),
    78: ("command-injection", "OS command injection"),
    22: ("path-traversal", "path traversal"),
    79: ("xss", "cross-site scripting"),
    94: ("code-injection", "code injection"),
    601: ("open-redirect", "open redirect"),
    918: ("ssrf", "server-side request forgery"),
    502: ("insecure-deserialization", "insecure deserialization"),
    943: ("nosql-injection", "NoSQL injection"),
    501: ("trust-boundary", "trust boundary violation"),
}
SEVERITY_JS = {89: "critical", 78: "critical", 94: "critical", 502: "critical", 943: "critical", 22: "major", 79: "major",
               918: "major", 601: "minor", 501: "minor"}


def sink_args(cwe: int, name: str, recv: str, argc: int, kw_keys: set[str] | None = None) -> list[int] | None:
    base = recv.split(".")[-1] if recv else ""
    root = re.split(r"[.(\[]", recv)[0] if recv else ""
    if cwe == 89:
        if name in ("query", "execute", "raw", "run", "all", "get", "each", "exec", "prepare") and argc >= 1:
            if name in ("get", "all", "run", "each", "exec", "prepare") and not any(k in recv.lower() for k in ("db", "conn", "pool", "client", "sqlite", "knex", "sequelize", "cursor", "pg", "mysql", "database")):
                return None
            if name in ("query", "execute", "raw"):
                return [0]
            return [0]
        return None
    if cwe == 78:
        if name in CHILD_PROC and (base in ("child_process", "cp", "proc", "childProcess", "") or recv == "") and argc >= 1:
            return [0]
        return None
    if cwe in (22, 23, 36, 73):
        if base in ("fs", "fsp", "promises", "fsPromises", "fse") and name in FS_ONE and argc >= 1:
            return [0]
        if base in ("fs", "fsp", "promises", "fse") and name in FS_TWO and argc >= 2:
            return [0, 1]
        if name in ("sendFile", "download", "sendfile") and argc >= 1:
            return [0]
        return None
    if cwe == 79:
        if name in ("send", "write", "end") and argc >= 1 and (base in ("res", "response", "reply", "ctx") or root in ("res", "response", "reply", "ctx") or recv.endswith(".res")):
            if re.search(r"\.(json|jsonp)\(|type\(\s*['\"](json|text/plain|plain|application/json)|contentType\(\s*['\"](json|text/plain)", recv):
                return None  # the response is declared JSON or plain text
            return [0]
        if name == "write" and base == "document" and argc >= 1:
            return [0]
        return None
    if cwe == 94:
        if name in ("eval", "Function", "runInNewContext", "runInThisContext", "runInContext", "compileFunction") and argc >= 1:
            return [0]
        if name in ("setTimeout", "setInterval", "setImmediate") and argc >= 1 and not recv:
            return [0]
        return None
    if cwe == 601:
        if name == "redirect" and argc >= 1 and (base in ("res", "response", "reply", "ctx") or root in ("res", "response", "reply", "ctx")):
            return [argc - 1] if argc <= 2 and argc == 2 else [0]
        return None
    if cwe == 918:
        if name in HTTP_METHODS and argc >= 1 and (base in HTTP_CLIENT_RECV or recv == ""):
            return [0]
        return None
    if cwe == 502:
        if name in ("unserialize", "deserialize") and argc >= 1:
            return [0]
        if name == "load" and base in ("yaml", "jsyaml") and argc >= 1:
            return [0]
        return None
    if cwe == 943:
        return None
    return None
