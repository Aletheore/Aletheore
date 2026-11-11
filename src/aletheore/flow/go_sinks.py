"""Sources, sinks and sanitizers for Go discover and validate modes."""

from __future__ import annotations

REQUEST_TYPES = {"Request", "Context"}          # *http.Request, *gin.Context, echo.Context, *fiber.Ctx (Ctx)
REQUEST_PARAM_TYPES = {"*http.Request", "*gin.Context", "echo.Context", "*fiber.Ctx", "*fasthttp.RequestCtx", "*mux.Router", "iris.Context", "*iris.Context", "*macaron.Context", "*context.Context"}
RESPONSE_PARAM_TYPES = {"http.ResponseWriter", "*http.ResponseWriter"}
REQUEST_METHODS = {"FormValue", "PostFormValue", "Cookie", "Cookies", "Referer", "UserAgent", "BasicAuth", "ParseForm", "ParseMultipartForm", "FormFile",
                   "Query", "DefaultQuery", "QueryArray", "QueryMap", "Param", "Params", "PostForm", "DefaultPostForm", "GetHeader", "GetRawData",
                   "ShouldBind", "ShouldBindJSON", "ShouldBindQuery", "Bind", "BindJSON", "BindQuery", "QueryParam", "QueryParams", "QueryString",
                   "FormParams", "Header", "Body", "URL", "RequestURI", "Host", "RemoteAddr", "Form", "PostForm", "MultipartForm", "Trailer", "Path",
                   "Get", "Cookie", "Query"}
# Methods that parse the request body or query into the value they are handed (`c.ShouldBindJSON(&req)`):
# the pointed-to variable then holds request data.
BINDERS = {"Bind", "BindJSON", "BindQuery", "BindXML", "BindYAML", "BindForm", "BindUri", "BindHeader", "BindWith",
           "ShouldBind", "ShouldBindJSON", "ShouldBindQuery", "ShouldBindXML", "ShouldBindYAML", "ShouldBindUri",
           "ShouldBindHeader", "ShouldBindWith", "ShouldBindBodyWith", "BodyParser", "QueryParser", "ParamsParser",
           "ReqHeaderParser", "ReadJSON", "ReadQuery", "ReadForm", "ReadBody", "ReadURL", "Decode"}
# Packages whose Unmarshal(data, &v) fills v from data.
UNMARSHAL_PKGS = {"json", "xml", "yaml", "toml", "msgpack", "jsoniter", "sonic", "easyjson", "proto", "protojson", "ini", "hcl"}
# A struct that holds one of these is the framework's per-request context.
REQUEST_HOLDERS = ("http.Request", "gin.Context", "echo.Context", "fiber.Ctx", "fasthttp.RequestCtx", "iris.Context")
REQUEST_FIELDS = {"Form", "PostForm", "MultipartForm", "Header", "Body", "URL", "RequestURI", "Host", "Referer", "RemoteAddr", "Trailer", "Cookies", "Params"}

SANITIZERS: dict[int, set[str]] = {
    79: {"EscapeString", "HTMLEscapeString", "HTMLEscaper", "JSEscapeString", "QueryEscape", "PathEscape", "Sanitize", "SanitizeBytes", "UGCPolicy", "Clean"},
    89: {"QuoteIdentifier", "QuoteLiteral", "Quote", "EscapeString"},
    78: {"Quote", "ShellEscape", "Escape"},
    22: {"Base", "Rel", "Sanitize", "SanitizeFilename", "SecureJoin"},
    601: {"IsLocalURL", "IsSafeRedirect"},
    918: {"IsSafeURL"},
}
SAFE_FUNCS = {"Atoi", "ParseInt", "ParseUint", "ParseFloat", "ParseBool", "Itoa", "FormatInt", "FormatUint", "FormatBool", "Len", "len", "cap", "Now", "Unix",
              "UnixNano", "New", "NewV4", "NewString", "Sum256", "Sum", "EncodeToString", "Intn", "Int", "Int63", "Float64", "ReadFull_x", "Equal", "EqualFold",
              "HasPrefix", "HasSuffix", "Contains", "ContainsAny", "Index", "LastIndex", "Count", "Compare", "IsDigit", "IsLetter", "Getpid", "Hostname"}
PURE_PKGS = {"strings", "strconv", "fmt", "path", "filepath", "bytes", "url", "utf8", "unicode", "json", "base64", "hex", "md5", "sha1", "sha256", "time", "math", "rand", "regexp", "errors", "sort", "reflect"}

SHELLS = {"sh", "bash", "zsh", "/bin/sh", "/bin/bash", "/usr/bin/sh", "/usr/bin/bash", "cmd", "cmd.exe", "powershell", "pwsh", "dash"}
SQL_HINTS = ("db", "tx", "conn", "sql", "database", "gorm", "sqlx", "store", "repo", "stmt", "pool", "dbx", "orm", "session")
PREPARED_RECEIVERS = {"stmt", "st", "prepared", "ps", "pstmt", "preparedstmt", "stmts", "stmt1", "stmt2", "namedstmt"}
SQL_ARG0 = {"Query", "QueryRow", "Exec", "Prepare", "Raw", "Select", "Get", "NamedQuery", "NamedExec", "MustExec", "Queryx", "QueryRowx", "Preparex", "Where", "Having", "Order", "Group", "Joins"}
SQL_ARG1 = {"QueryContext", "QueryRowContext", "ExecContext", "PrepareContext", "QueryxContext", "GetContext", "SelectContext", "NamedQueryContext", "NamedExecContext"}
# Stat, Lstat and Readlink are existence or metadata checks, not file access, and are deliberately left out.
OS_FILE_ONE = {"Open", "OpenFile", "ReadFile", "WriteFile", "Create", "Remove", "RemoveAll", "Mkdir", "MkdirAll", "ReadDir", "Chmod", "Chown", "Truncate"}
IOUTIL_FILE = {"ReadFile", "WriteFile", "ReadDir", "TempFile_x"}
HTTP_CLIENT_FUNCS = {"Get", "Post", "PostForm", "Head"}

DISCOVER_CWES_GO = (89, 78, 22, 79, 601, 918, 90, 643, 94, 502, 501)
CWE_NAMES_GO = {
    89: ("sql-injection", "SQL injection"),
    78: ("command-injection", "OS command injection"),
    22: ("path-traversal", "path traversal"),
    79: ("xss", "cross-site scripting"),
    601: ("open-redirect", "open redirect"),
    918: ("ssrf", "server-side request forgery"),
    90: ("ldap-injection", "LDAP injection"),
    643: ("xpath-injection", "XPath injection"),
    94: ("code-injection", "code injection"),
    502: ("insecure-deserialization", "insecure deserialization"),
    501: ("trust-boundary", "trust boundary violation"),
}
SEVERITY_GO = {89: "critical", 78: "critical", 94: "critical", 502: "critical", 22: "major", 79: "major", 918: "major", 90: "major", 643: "major",
               601: "minor", 501: "minor"}


def sink_args(cwe: int, name: str, recv: str, argc: int, first_const: str | None = None) -> list[int] | None:
    """Argument positions that must not be attacker controlled. `recv` is the receiver or package text."""
    base = recv.split(".")[-1].lower() if recv else ""
    if cwe == 89:
        if base in PREPARED_RECEIVERS:
            return None  # a prepared statement's Query/Exec take bound PARAMETERS, not SQL text
        hinted = any(h in base or h in recv.lower() for h in SQL_HINTS)
        if name in SQL_ARG0 and argc >= 1 and hinted:
            return [0]
        if name in SQL_ARG1 and argc >= 2 and hinted:
            return [1]
        return None
    if cwe == 78:
        if recv.split(".")[-1] == "exec" and name in ("Command", "CommandContext") and argc >= 1:
            i = 1 if name == "CommandContext" else 0
            if first_const is not None and first_const in SHELLS:
                return list(range(i, argc))
            return [i] if argc > i else None
        if recv.split(".")[-1] == "syscall" and name in ("Exec", "ForkExec") and argc >= 1:
            return [0]
        return None
    if cwe in (22, 23, 36, 73):
        b = recv.split(".")[-1]
        if b == "os" and name in OS_FILE_ONE and argc >= 1:
            return [0]
        if b == "os" and name == "Rename" and argc >= 2:
            return [0, 1]
        if b in ("ioutil", "os") and name in IOUTIL_FILE and argc >= 1:
            return [0]
        if b == "http" and name == "ServeFile" and argc >= 3:
            return [2]
        if b in ("filepath", "fs") and name in ("Walk", "WalkDir", "Glob") and argc >= 1:
            return [0]
        if name in ("SaveUploadedFile",) and argc >= 2:
            return [1]
        if name in ("File", "Attachment", "Static") and argc >= 1 and base in ("c", "ctx", "context"):
            return [0]
        return None
    if cwe == 79:
        b = recv.split(".")[-1]
        if b == "fmt" and name in ("Fprintf",) and argc >= 2:
            return list(range(1, argc))
        if b == "fmt" and name in ("Fprint", "Fprintln") and argc >= 2:
            return list(range(1, argc))
        if b == "io" and name == "WriteString" and argc >= 2:
            return [1]
        if b in ("template",) and name in ("HTML", "JS", "JSStr", "HTMLAttr", "CSS", "URL") and argc >= 1:
            return [0]
        if name == "Write" and argc >= 1 and base in ("w", "rw", "res", "resp", "writer", "response", "responsewriter"):
            return [0]
        if name in ("String", "HTML", "Data") and base in ("c", "ctx", "context") and argc >= 2:
            return list(range(1, argc)) if name != "Data" else [2]
        return None
    if cwe == 601:
        if recv.split(".")[-1] == "http" and name == "Redirect" and argc >= 3:
            return [2]
        if name in ("Redirect",) and base in ("c", "ctx", "context") and argc >= 2:
            return [1]
        return None
    if cwe == 918:
        b = recv.split(".")[-1]
        if b == "http" and name in HTTP_CLIENT_FUNCS and argc >= 1:
            return [0]
        if b == "http" and name == "NewRequest" and argc >= 2:
            return [1]
        if b == "http" and name == "NewRequestWithContext" and argc >= 3:
            return [2]
        if b == "net" and name in ("Dial", "DialTimeout") and argc >= 2:
            return [1]
        if name in ("Get", "Post", "Head") and argc >= 1 and base in ("client", "httpclient", "c") and recv:
            return [0]
        return None
    if cwe == 90:
        if recv.split(".")[-1] == "ldap" and name == "NewSearchRequest" and argc >= 7:
            return [0, 6]
        return None
    if cwe == 643:
        if recv.split(".")[-1] in ("xpath", "xmlquery", "htmlquery") and name in ("Compile", "MustCompile", "Find", "FindOne", "Query") and argc >= 1:
            return [argc - 1] if name in ("Find", "FindOne", "Query") and argc >= 2 else [0]
        return None
    if cwe == 94:
        if recv.split(".")[-1] == "plugin" and name == "Open" and argc >= 1:
            return [0]
        return None
    if cwe == 502:
        b = recv.split(".")[-1]
        if b in ("yaml", "gob") and name in ("Unmarshal", "UnmarshalStrict") and argc >= 1:
            return [0]
        return None
    return None


# Weakness classes shared by every language (template injection, regex injection, reflection, expression
# language, NoSQL), from extra_sinks.
from aletheore.flow import extra_sinks as _X  # noqa: E402

_X.ADDED["go"] = {c for c in _X.new_classes("go") if c not in DISCOVER_CWES_GO}
DISCOVER_CWES_GO = DISCOVER_CWES_GO + tuple(c for c in _X.new_classes("go") if c not in DISCOVER_CWES_GO)
for _c in _X.new_classes("go"):
    CWE_NAMES_GO.setdefault(_c, _X.CLASS_NAMES[_c])
    SEVERITY_GO.setdefault(_c, _X.CLASS_SEVERITY[_c])
    SANITIZERS.setdefault(_c, set())
