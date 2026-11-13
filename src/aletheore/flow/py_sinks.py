"""Sources, sinks and sanitizers for Python discover mode.

`sink_args` returns the argument positions that must not be attacker controlled for a call, or None
when it is not a sink for that weakness class. Sanitizers are per class: an HTML escape does not make
a value safe for a SQL query.
"""

from __future__ import annotations

REQUEST_ATTRS_SAFE = {"method", "is_json", "is_secure", "scheme", "endpoint", "blueprint"}
SYS_SOURCES = {"argv"}

SANITIZERS: dict[int, set[str]] = {
    79: {"escape", "html_escape", "conditional_escape", "escape_html", "bleach_clean", "clean", "strip_tags",
         "urlencode", "quote", "quote_plus", "json_script", "escapejs", "force_escape"},
    78: {"quote", "shlex_quote"},
    89: set(),
    22: {"basename", "secure_filename", "safe_join"},
    90: {"escape_filter_chars", "escape_dn_chars", "filter_format"},
    643: set(),
    501: set(),
    94: {"literal_eval"},
    502: set(),
    918: set(),
    601: {"url_has_allowed_host_and_scheme", "is_safe_url", "safe_redirect"},
}
# Calls whose result is a number, boolean or other benign value derived from the input.
SAFE_RESULT_CALLS = {"int", "float", "bool", "len", "isinstance", "hasattr", "callable", "id", "hash", "abs", "round",
                     "ord", "chr", "UUID", "isdigit", "isalpha", "isalnum", "isnumeric", "startswith", "endswith", "isdecimal"}

ROUTE_MARKERS = (".route(", ".get(", ".post(", ".put(", ".delete(", ".patch(", ".head(", ".options(", ".websocket(",
                 "api_view(", "require_http_methods(", "app.route", "@route", "expose")

OS_CMD = {"system", "popen", "popen2", "popen3", "popen4", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
          "spawnve", "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe"}
SUBPROCESS = {"call", "run", "Popen", "check_output", "check_call", "getoutput", "getstatusoutput"}
OS_FILE_ONE = {"remove", "unlink", "rmdir", "removedirs", "listdir", "mkdir", "makedirs", "stat", "chmod", "chdir",
               "walk", "scandir", "readlink", "lstat", "utime", "truncate"}
SHUTIL_ONE = {"rmtree", "make_archive", "unpack_archive"}
SHUTIL_TWO = {"copy", "copy2", "copyfile", "copytree", "move", "copymode", "copystat"}
PATH_CONSUMERS = {"read_text", "read_bytes", "open", "write_text", "write_bytes", "unlink", "rmdir", "mkdir", "rename",
                  "replace", "touch", "iterdir", "glob", "rglob"}
HTTP_CLIENTS = {"get", "post", "put", "delete", "patch", "head", "options", "request", "urlopen", "urlretrieve"}
HTTP_RECEIVERS = {"requests", "httpx", "urllib", "urllib2", "request", "aiohttp", "session", "client", "http"}
DESER = {("pickle", "loads"), ("pickle", "load"), ("cPickle", "loads"), ("cPickle", "load"), ("marshal", "loads"),
         ("marshal", "load"), ("dill", "loads"), ("dill", "load"), ("jsonpickle", "decode"), ("shelve", "open")}

DISCOVER_CWES_PY = (89, 78, 22, 79, 90, 643, 501, 94, 502, 918, 601)
CWE_NAMES_PY = {
    89: ("sql-injection", "SQL injection"),
    78: ("command-injection", "OS command injection"),
    22: ("path-traversal", "path traversal"),
    79: ("xss", "cross-site scripting"),
    90: ("ldap-injection", "LDAP injection"),
    643: ("xpath-injection", "XPath injection"),
    501: ("trust-boundary", "trust boundary violation"),
    94: ("code-injection", "code injection"),
    502: ("insecure-deserialization", "insecure deserialization"),
    918: ("ssrf", "server-side request forgery"),
    601: ("open-redirect", "open redirect"),
}
SEVERITY_PY = {89: "critical", 78: "critical", 94: "critical", 502: "critical", 22: "major", 79: "major", 90: "major",
               643: "major", 918: "major", 601: "minor", 501: "minor"}


def sink_args(cwe: int, name: str, recv: str, argc: int, kw: dict, recv_tainted: bool = False) -> list[int] | None:
    base = recv.split(".")[-1] if recv else ""
    if cwe == 89:
        if name in ("execute", "executemany", "executescript", "mogrify", "raw", "exec_driver_sql", "execute_statement", "execute_sql", "run_sql", "callproc", "execute_raw") and argc >= 1:
            return [0]
        if name in ("sql", "query", "exec", "read_sql_x") and argc >= 1 and base.lower() in ("spark", "duckdb", "con", "conn", "connection", "db", "database", "client", "bq", "bigquery", "warehouse", "engine", "session", "pool", "cursor", "cur"):
            return [0]  # spark.sql(q), duckdb.sql(q), client.query(q), session.exec(q): the string is the statement
        return None
    if cwe == 78:
        if base == "os" and name in OS_CMD and argc >= 1:
            return [0]
        if base == "subprocess" and name in SUBPROCESS and argc >= 1:
            return [0]
        if base in ("commands",) and name in ("getoutput", "getstatusoutput"):
            return [0]
        if not recv and name in ("Popen", "check_output", "check_call") and argc >= 1:
            return [0]
        return None
    if cwe in (22, 23, 36, 73):
        if not recv and name == "open" and argc >= 1:
            return [0]
        if base == "os" and name in OS_FILE_ONE and argc >= 1:
            return [0]
        if base == "os" and name in ("rename", "replace", "symlink", "link") and argc >= 2:
            return [0, 1]
        if base == "shutil" and name in SHUTIL_ONE and argc >= 1:
            return [0]
        if base == "shutil" and name in SHUTIL_TWO and argc >= 2:
            return [0, 1]
        # send_from_directory is deliberately not a sink: it joins the name with safe_join.
        if name in ("send_file", "FileResponse", "FileSystemStorage") and argc >= 1:
            return [0]
        if name in PATH_CONSUMERS and recv_tainted:
            return []
        return None
    if cwe == 79:
        if name in ("Markup", "mark_safe", "HttpResponse", "make_response", "render_template_string", "HTMLResponse") and argc >= 1:
            return [0]
        if name == "Response" and argc >= 1:
            ct = kw.get("mimetype") or kw.get("content_type")
            if ct is None or "html" in str(getattr(ct, "value", "html")).lower():
                return [0]  # a Flask/Starlette Response is HTML unless it says otherwise
        return None
    if cwe == 90:
        if name == "search_s" and argc >= 3:
            return [0, 2]
        if name == "search_ext_s" and argc >= 3:
            return [0, 2]
        if name == "search" and argc >= 2 and "ldap" in recv.lower():
            return [0, 1]
        return None
    if cwe == 643:
        if name == "xpath" and argc >= 1:
            return [0]
        if name == "XPath" and argc >= 1 and base == "etree":
            return [0]
        return None
    if cwe == 94:
        if not recv and name in ("eval", "exec", "compile") and argc >= 1:
            return [0]
        if name in ("import_module", "__import__") and argc >= 1 and base in ("", "importlib"):
            return [0]
        return None
    if cwe == 502:
        if (base, name) in DESER and argc >= 1:
            return [0]
        if name == "load" and base == "yaml" and argc >= 1 and "Loader" not in kw and "SafeLoader" not in str(kw):
            return [0]
        if name == "loads" and base == "yaml" and argc >= 1:
            return [0]
        return None
    if cwe == 918:
        if name in HTTP_CLIENTS and argc >= 1 and (base in HTTP_RECEIVERS or base.endswith("session")):
            return [0]
        return None
    if cwe == 601:
        if name in ("redirect", "HttpResponseRedirect", "RedirectResponse") and argc >= 1 and not recv:
            return [0]
        return None
    return None


# Weakness classes shared by every language (template injection, regex injection, reflection, expression
# language, NoSQL), from extra_sinks.
from aletheore.flow import extra_sinks as _X  # noqa: E402

_X.ADDED["python"] = {c for c in _X.new_classes("python") if c not in DISCOVER_CWES_PY}
DISCOVER_CWES_PY = DISCOVER_CWES_PY + tuple(c for c in _X.new_classes("python") if c not in DISCOVER_CWES_PY)
for _c in _X.new_classes("python"):
    CWE_NAMES_PY.setdefault(_c, _X.CLASS_NAMES[_c])
    SEVERITY_PY.setdefault(_c, _X.CLASS_SEVERITY[_c])
    SANITIZERS.setdefault(_c, set())
