"""Sink catalog for Java discover mode.

`sink_args(cwe, name, cname, recv_type, recv_text, argc)` returns the argument positions whose value
must not be attacker controlled for that call, or None when the call is not a sink for that class.
Receiver types come from declared types and from a few modeled factories (request.getSession(),
response.getWriter(), connection.createStatement()); where the type is unknown a method name that is
specific enough (executeQuery with a string argument) still counts.
"""

from __future__ import annotations

SQL_RECEIVERS = {"Statement", "JdbcTemplate", "NamedParameterJdbcTemplate", "JdbcOperations", "EntityManager",
                 "Session", "SessionFactory", "Connection", "JdbcTemplateWrapper", "SimpleJdbcTemplate"}
WRITER_RECEIVERS = {"PrintWriter", "ServletOutputStream", "JspWriter", "Writer", "BufferedWriter", "OutputStreamWriter"}
# Constructing a File or a Path is not a file operation; opening, reading, writing or deleting is.
# The stream constructors below open the file. A tainted File/Path value is a sink when it is USED.
FILE_CTORS = {"FileInputStream", "FileOutputStream", "FileReader", "FileWriter", "RandomAccessFile"}
FILE_USING_METHODS = {"delete", "deleteOnExit", "exists", "createNewFile", "list", "listFiles", "mkdir", "mkdirs",
                      "renameTo", "length", "isFile", "isDirectory", "canRead", "canWrite", "lastModified",
                      "setReadable", "setWritable", "toURI"}
LDAP_RECEIVERS = {"DirContext", "LdapContext", "InitialDirContext", "InitialLdapContext", "LdapTemplate"}
XPATH_RECEIVERS = {"XPath", "XPathExpression"}
# java.nio.file.Files methods, with the argument positions that are PATHS (the rest is content).
FILES_PATH_ARGS = {
    "readAllBytes": [0], "readString": [0], "readAllLines": [0], "newBufferedReader": [0], "newBufferedWriter": [0],
    "newInputStream": [0], "newOutputStream": [0], "write": [0], "writeString": [0], "delete": [0],
    "deleteIfExists": [0], "createFile": [0], "createDirectory": [0], "createDirectories": [0], "lines": [0],
    "list": [0], "walk": [0], "copy": [0, 1], "move": [0, 1],
}


def _all(argc: int) -> list[int]:
    return list(range(argc))


def sink_args(cwe: int, name: str, cname: str | None, recv_type: str | None, recv_text: str, argc: int,
              recv_tainted: bool = False) -> list[int] | None:
    if cwe == 89:
        # executeQuery/executeUpdate are specific enough to trust without a type; plain execute and
        # addBatch are common method names, so they need a known JDBC receiver.
        if name in ("executeQuery", "executeUpdate", "executeLargeUpdate") and argc >= 1:
            if recv_type is None or recv_type in SQL_RECEIVERS:
                return [0]
        if name in ("execute", "addBatch") and argc >= 1 and recv_type in SQL_RECEIVERS:
            return [0]
        if name in ("prepareStatement", "prepareCall", "nativeSQL") and argc >= 1 and recv_type in (None, "Connection"):
            return [0]
        if name in ("createQuery", "createNativeQuery", "createSQLQuery", "createStoredProcedureQuery") and argc >= 1:
            return [0]
        if recv_type in ("JdbcTemplate", "NamedParameterJdbcTemplate", "JdbcOperations", "SimpleJdbcTemplate") and argc >= 1 \
                and name in ("query", "queryForObject", "queryForList", "queryForMap", "queryForRowSet", "update",
                             "batchUpdate", "execute", "queryForInt", "queryForLong"):
            return [0]
        return None
    if cwe == 78:
        if name == "exec" and argc >= 1 and (recv_type == "Runtime" or recv_text.endswith("getRuntime()")):
            return [0]
        if name == "command" and argc >= 1 and recv_type == "ProcessBuilder":
            return _all(argc)
        if name == "<init>" and cname == "ProcessBuilder":
            return _all(argc)
        return None
    if cwe in (22, 23, 36, 73):
        if name == "<init>" and cname in FILE_CTORS:
            return _all(argc)
        if cname == "Files" and name in FILES_PATH_ARGS:
            return [i for i in FILES_PATH_ARGS[name] if i < argc]
        if recv_type == "File" and name in FILE_USING_METHODS and recv_tainted:
            return []  # the receiver itself is tainted; the caller checks `recv_tainted` and reports
        if name in ("getResourceAsStream", "getResource", "getRequestDispatcher") and argc >= 1:
            return [0]
        return None
    if cwe == 79:
        if recv_type in WRITER_RECEIVERS:
            if name in ("print", "println", "append") and argc >= 1:
                return [0]
            if name == "write" and argc >= 1:
                return [0]
            if name in ("printf", "format") and argc >= 1:
                return _all(argc)
        return None
    if cwe == 90:
        if name == "search" and argc >= 2 and recv_type in LDAP_RECEIVERS:
            return [0, 1]
        return None
    if cwe == 643:
        if name in ("evaluate", "compile") and argc >= 1 and (recv_type in XPATH_RECEIVERS or "xpath" in recv_text.lower()):
            return [0]
        if name in ("selectNodes", "selectSingleNode", "valueOf") and argc >= 1 and "xpath" not in recv_text.lower() and recv_type in ("Document", "Node", "Element"):
            return [0]
        return None
    if cwe == 501:
        if name in ("setAttribute", "putValue") and argc >= 1 and recv_type == "HttpSession":
            return _all(argc)
        return None
    return None


DISCOVER_CWES = (89, 78, 22, 79, 90, 643, 501)
CWE_NAMES = {
    89: ("sql-injection", "SQL injection"),
    78: ("command-injection", "OS command injection"),
    22: ("path-traversal", "path traversal"),
    79: ("xss", "cross-site scripting"),
    90: ("ldap-injection", "LDAP injection"),
    643: ("xpath-injection", "XPath injection"),
    501: ("trust-boundary", "trust boundary violation"),
}


# Which argument positions carry the attacker-relevant value for a named sink method, used when only a
# finding's location is known (validate mode). None means "judge every argument".
_PROBE_POSITIONS = {
    89: {"execute": [0], "executeQuery": [0], "executeUpdate": [0], "executeLargeUpdate": [0], "addBatch": [0],
         "prepareStatement": [0], "prepareCall": [0], "createQuery": [0], "createNativeQuery": [0],
         "createSQLQuery": [0], "nativeSQL": [0]},
    78: {"exec": [0]},
    90: {"search": [0, 1]},
    643: {"evaluate": [0], "compile": [0], "selectNodes": [0], "selectSingleNode": [0]},
    79: {"print": [0], "println": [0], "append": [0], "write": [0]},
}


def probe_positions(cwes: set[int], name: str, argc: int) -> list[int] | None:
    found: set[int] = set()
    for cwe in cwes:
        pos = _PROBE_POSITIONS.get(cwe, {}).get(name)
        if pos is not None:
            found.update(i for i in pos if i < argc)
    return sorted(found) if found else None
