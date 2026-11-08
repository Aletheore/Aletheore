"""Sources, sinks and sanitizers for Kotlin discover and validate modes (Spring, Ktor, servlets, JDBC, Exposed)."""

from __future__ import annotations

PARAM_ANNOTATIONS = {"RequestParam", "PathVariable", "RequestBody", "RequestHeader", "CookieValue", "ModelAttribute", "RequestPart", "MatrixVariable", "QueryParam",
                     "PathParam", "FormParam", "HeaderParam", "CookieParam", "BeanParam"}
MAPPING_ANNOTATIONS = {"GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "PatchMapping", "RequestMapping", "GET", "POST", "PUT", "DELETE", "PATCH", "Path"}
CONTROLLER_ANNOTATIONS = {"RestController", "Controller", "Path", "WebServlet"}
REQUEST_TYPES = {"HttpServletRequest", "ServletRequest", "HttpRequest", "ServerHttpRequest", "ServerRequest", "ApplicationCall", "WebRequest", "NativeWebRequest", "RoutingCall"}
SAFE_PARAM_TYPES = {"Int", "Long", "Short", "Byte", "Boolean", "Double", "Float", "UUID", "BigDecimal", "BigInteger", "LocalDate", "LocalDateTime", "Instant", "Unit", "Char"}
SERVLET_BENIGN = {"getMethod", "method", "isSecure", "getScheme", "scheme", "getServerName", "serverName", "getServerPort", "serverPort", "getRemoteAddr", "remoteAddr",
                  "getContextPath", "contextPath", "getSession", "session", "getLocale", "locale", "isUserInRole", "getUserPrincipal", "userPrincipal", "getProtocol", "protocol"}

SANITIZERS: dict[int, set[str]] = {
    79: {"escapeHtml", "escapeHtml4", "escapeHtml3", "htmlEscape", "encodeForHTML", "encodeForHTMLAttribute", "encodeForJavaScript", "escapeXml", "escapeXml10",
         "escapeXml11", "escapeJavaScript", "escapeEcmaScript", "encodeForCSS", "encodeForURL", "sanitize", "forHtml", "forHtmlContent", "forHtmlAttribute", "forJavaScript",
         "htmlEscapeDecimal", "htmlEscapeHex", "escape_x"},
    89: {"encodeForSQL", "escapeSql", "escapeSQL"},
    78: set(),
    22: {"getName", "getBaseName", "getFilename", "sanitizeFilename"},
    601: set(),
}
SAFE_METHODS = {"toInt", "toLong", "toDouble", "toFloat", "toBoolean", "toShort", "toByte", "toIntOrNull", "toLongOrNull", "toDoubleOrNull", "toBooleanOrNull", "toBigDecimal",
                "toBigInteger", "toUInt", "size", "length", "isEmpty", "isNotEmpty", "isBlank", "isNotBlank", "isNullOrEmpty", "isNullOrBlank", "contains", "startsWith", "endsWith",
                "equals", "equalsIgnoreCase", "hashCode", "compareTo", "indexOf", "lastIndexOf", "containsKey", "containsValue", "matches", "any", "all", "none", "count",
                "parseInt", "parseLong", "parseDouble", "valueOf_x", "fromString", "randomUUID", "currentTimeMillis", "nanoTime", "now", "isDigit", "isLetter", "isLetterOrDigit",
                "ordinal", "isNull", "isPresent", "toUUID", "min", "max", "abs", "coerceAtLeast", "coerceAtMost", "coerceIn"}
PURE_TYPES = {"String", "Integer", "Long", "Double", "Float", "Boolean", "Math", "UUID", "LocalDate", "LocalDateTime", "Instant", "Objects", "Arrays", "Collections", "Optional",
              "Character", "BigDecimal", "BigInteger", "URLEncoder", "URLDecoder", "Base64", "StringUtils", "Regex", "Pattern", "System", "Duration"}
DB_RECV_HINTS = ("jdbc", "stmt", "statement", "template", "entitymanager", "em", "session", "db", "conn", "connection", "sql", "dsl", "database", "dao", "repo_x", "query")

DISCOVER_CWES_KT = (89, 78, 22, 79, 601, 918, 502, 94, 643, 90, 501)
CWE_NAMES_KT = {
    89: ("sql-injection", "SQL injection"),
    78: ("command-injection", "OS command injection"),
    22: ("path-traversal", "path traversal"),
    79: ("xss", "cross-site scripting"),
    601: ("open-redirect", "open redirect"),
    918: ("ssrf", "server-side request forgery"),
    502: ("insecure-deserialization", "insecure deserialization"),
    94: ("code-injection", "code injection"),
    643: ("xpath-injection", "XPath injection"),
    90: ("ldap-injection", "LDAP injection"),
    501: ("trust-boundary", "trust boundary violation"),
}
SEVERITY_KT = {89: "critical", 78: "critical", 94: "critical", 502: "critical", 22: "major", 79: "major", 918: "major", 643: "major", 90: "major", 601: "minor", 501: "minor"}


def sink_args(cwe: int, name: str, recv: str, argc: int) -> list[int] | None:
    """Positions of the sink's judged arguments, None when this call is not a sink for `cwe`. Constructor calls arrive as `<new>`."""
    r = recv.lower()
    base = recv.split(".")[-1].rstrip("()") if recv else ""
    if cwe == 89:
        if name in ("executeQuery", "executeUpdate", "execute", "addBatch", "executeLargeUpdate", "prepareStatement", "prepareCall", "nativeQuery", "createNativeQuery",
                    "createQuery", "createSQLQuery", "createSqlQuery", "rawQuery", "execSQL", "queryForObject", "queryForList", "queryForMap", "queryForRowSet", "queryForStream",
                    "query", "update", "batchUpdate", "exec", "selectAll_x") and argc >= 1:
            if name in ("execute", "exec", "query", "update") and not any(h in r for h in DB_RECV_HINTS):
                return None
            return [0]
        return None
    if cwe == 78:
        if name == "exec" and "runtime" in r and argc >= 1:
            return [0]
        if name in ("ProcessBuilder", "<new>") and base == "ProcessBuilder" or (name == "ProcessBuilder" and not recv):
            return list(range(min(argc, 3))) if argc else None
        if name == "command" and argc >= 1 and "builder" in r:
            return [0]
        return None
    if cwe in (22, 23, 36, 73):
        if (not recv or recv.startswith(("java.io", "java.nio"))) and name in ("File", "FileInputStream", "FileOutputStream", "FileReader", "FileWriter", "RandomAccessFile", "FileSystemResource", "FileInputStream") and argc >= 1:
            return [0, 1] if argc >= 2 else [0]
        if base in ("Paths", "Path") and name in ("get", "of") and argc >= 1:
            return list(range(argc))
        if base == "Files" and name in ("readAllBytes", "readAllLines", "readString", "newInputStream", "newBufferedReader", "write", "writeString", "delete", "deleteIfExists", "copy", "move", "createFile", "createDirectory", "createDirectories", "lines", "list", "walk", "newOutputStream", "newBufferedWriter") and argc >= 1:
            return [0, 1] if name in ("copy", "move") and argc >= 2 else [0]
        if name in ("resolve",) and argc >= 1 and any(h in r for h in ("file", "dir", "path", "folder", "root", "upload")):
            return [0]
        if name in ("readText", "readBytes", "writeText", "writeBytes", "appendText", "deleteRecursively", "copyTo", "forEachLine", "bufferedReader", "inputStream", "outputStream") and not argc and False:
            return None
        return None
    if cwe == 79:
        if name in ("write", "print", "println", "append", "printf", "format") and any(h in r for h in ("getwriter", "writer", "response.outputstream", "out")) and argc >= 1 and "log" not in r:
            return [0]
        return None
    if cwe == 601:
        if name in ("sendRedirect", "respondRedirect", "redirect") and argc >= 1:
            return [0]
        if name in ("RedirectView", "<new>") and base == "RedirectView" and argc >= 1:
            return [0]
        if name == "RedirectView" and not recv and argc >= 1:
            return [0]
        if name in ("setLocation", "location") and argc >= 1 and ("header" in r or "response" in r):
            return [0]
        return None
    if cwe == 918:
        if not recv and name in ("URL", "URI") and argc >= 1:
            return [0]
        if base in ("URI", "URL") and name in ("create", "<new>") and argc >= 1:
            return [0]
        if name in ("getForObject", "getForEntity", "postForObject", "postForEntity", "exchange", "headForHeaders", "postForLocation", "execute", "put", "delete", "optionsForAllow") and "rest" in r and argc >= 1:
            return [0]
        if name in ("HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpHead", "HttpPatch") and not recv and argc >= 1:
            return [0]
        if name == "url" and argc >= 1 and ("builder" in r or "request" in r):
            return [0]
        if name == "uri" and argc >= 1 and ("webclient" in r or "get()" in r or "post()" in r or "client" in r):
            return [0]
        if name in ("get", "post", "put", "delete", "head", "patch") and argc >= 1 and any(h in r for h in ("httpclient", "client")) and False:
            return [0]
        return None
    if cwe == 502:
        if name in ("ObjectInputStream",) and not recv and argc >= 1:
            return [0]
        if name in ("fromXML",) and argc >= 1:
            return [0]
        if name in ("load", "loadAs", "loadAll") and "yaml" in r and argc >= 1:
            return [0]
        return None
    if cwe == 94:
        if name in ("eval", "evaluate") and argc >= 1 and any(h in r for h in ("engine", "shell", "script", "groovy", "interpreter")):
            return [0]
        if name == "forName" and base == "Class" and argc >= 1:
            return [0]
        if name in ("parseExpression",) and argc >= 1:
            return [0]
        if name in ("loadClass",) and argc >= 1:
            return [0]
        if name == "getMethod" and argc >= 1 and False:
            return None
        return None
    if cwe == 643:
        if name in ("evaluate", "compile") and argc >= 1 and "xpath" in r:
            return [0]
        return None
    if cwe == 90:
        if name == "search" and argc >= 2 and any(h in r for h in ("ctx", "ldap", "context", "dir")):
            return [0, 1]
        return None
    return None
