"""Sources, sinks and sanitizers for C# discover and validate modes (ASP.NET, WebForms, ADO.NET, EF)."""

from __future__ import annotations

REQUEST_ROOTS = {"Request", "HttpContext.Request", "Context.Request", "request"}
REQUEST_PROPS = {"QueryString", "Form", "Params", "Cookies", "Headers", "Url", "RawUrl", "Path", "PathInfo", "Query", "Body", "InputStream", "Files",
                 "UserAgent", "UrlReferrer", "ServerVariables", "Browser", "QueryStringValues", "RouteValues", "Host", "PathBase", "QueryString"}
ACTION_ATTRS = {"FromQuery", "FromBody", "FromRoute", "FromForm", "FromHeader", "FromServices_x"}
HTTP_ATTRS = {"HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpPatch", "Route", "HttpHead", "HttpOptions", "AcceptVerbs"}
CONTROLLER_BASES = {"Controller", "ControllerBase", "ApiController", "Page", "PageModel", "System.Web.Mvc.Controller", "RazorPage"}

SANITIZERS: dict[int, set[str]] = {
    79: {"HtmlEncode", "HtmlAttributeEncode", "JavaScriptStringEncode", "UrlEncode", "UrlPathEncode", "EscapeDataString", "Encode", "Sanitize", "SanitizeHtml",
         "XmlEncode", "EncodeForHtml"},
    89: {"EscapeString", "QuoteName", "EscapeIdentifier"},
    78: set(),
    22: {"GetFileName", "GetFileNameWithoutExtension"},
    601: {"IsLocalUrl"},
}
SAFE_METHODS = {"Parse", "TryParse", "ToInt32", "ToInt64", "ToInt16", "ToDouble", "ToDecimal", "ToBoolean", "ToSingle", "ToByte", "NewGuid", "ToUInt32", "ToUInt64",
                "Length", "Count", "IsNullOrEmpty", "IsNullOrWhiteSpace", "Contains", "StartsWith", "EndsWith", "Equals", "Compare", "CompareTo", "IndexOf",
                "LastIndexOf", "Any", "All", "GetHashCode", "Now", "UtcNow", "Today", "Ticks", "ToBase64String_x", "ComputeHash", "NextInt_x", "Next", "NextDouble",
                "IsMatch", "IsDigit", "IsLetter", "IsLetterOrDigit", "Min", "Max", "Abs", "Floor", "Ceiling", "Round", "Sqrt", "Pow"}
PURE_TYPES = {"string", "String", "Path", "Convert", "Math", "Guid", "DateTime", "TimeSpan", "Regex", "Environment", "Encoding", "Uri", "Enumerable", "Array",
              "List", "Dictionary", "HashSet", "StringBuilder", "Int32", "Int64", "Double", "Decimal", "Boolean", "Char", "Server"}

SQL_COMMANDS = {"SqlCommand", "OleDbCommand", "MySqlCommand", "NpgsqlCommand", "OdbcCommand", "SQLiteCommand", "SqliteCommand", "OracleCommand", "FbCommand",
                "SqlDataAdapter", "OleDbDataAdapter", "MySqlDataAdapter", "OdbcDataAdapter", "NpgsqlDataAdapter", "SQLiteDataAdapter", "SqliteDataAdapter"}
SQL_METHODS = {"FromSqlRaw", "FromSqlInterpolated_x", "ExecuteSqlRaw", "ExecuteSqlCommand", "ExecuteSqlRawAsync", "SqlQuery", "SqlQueryRaw", "ExecuteSql_x",
               "Query", "QueryFirst", "QueryFirstOrDefault", "QuerySingle", "QuerySingleOrDefault", "Execute", "ExecuteScalar_x", "QueryAsync", "ExecuteAsync",
               "FromSql", "CreateSQLQuery", "CreateQuery_x", "ExecuteStoreQuery", "ExecuteStoreCommand"}
FILE_CTORS = {"FileStream", "StreamReader", "StreamWriter", "FileInfo", "DirectoryInfo", "BinaryReader_x", "XmlTextReader_x"}
FILE_STATICS = {"ReadAllText", "ReadAllBytes", "ReadAllLines", "ReadLines", "WriteAllText", "WriteAllBytes", "WriteAllLines", "AppendAllText", "AppendAllLines",
                "Open", "OpenRead", "OpenWrite", "OpenText", "Create", "CreateText", "Delete", "Copy", "Move", "Replace", "Move_x", "GetFiles", "GetDirectories",
                "GetFileSystemEntries", "EnumerateFiles", "EnumerateDirectories", "CreateDirectory", "SetAttributes", "ReadAllTextAsync", "WriteAllTextAsync"}
HTTP_CLIENT_METHODS = {"DownloadString", "DownloadData", "DownloadFile", "DownloadStringAsync", "DownloadStringTaskAsync", "UploadString", "UploadData", "UploadFile",
                       "GetAsync", "PostAsync", "PutAsync", "DeleteAsync", "GetStringAsync", "GetStreamAsync", "GetByteArrayAsync", "SendAsync_x", "OpenRead"}
DESER_TYPES = {"BinaryFormatter", "NetDataContractSerializer", "SoapFormatter", "LosFormatter", "ObjectStateFormatter", "DataContractSerializer_x"}

DISCOVER_CWES_CS = (89, 78, 22, 79, 601, 918, 502, 90, 643, 94, 501)
CWE_NAMES_CS = {
    89: ("sql-injection", "SQL injection"),
    78: ("command-injection", "OS command injection"),
    22: ("path-traversal", "path traversal"),
    79: ("xss", "cross-site scripting"),
    601: ("open-redirect", "open redirect"),
    918: ("ssrf", "server-side request forgery"),
    502: ("insecure-deserialization", "insecure deserialization"),
    90: ("ldap-injection", "LDAP injection"),
    643: ("xpath-injection", "XPath injection"),
    94: ("code-injection", "code injection"),
    501: ("trust-boundary", "trust boundary violation"),
}
SEVERITY_CS = {89: "critical", 78: "critical", 94: "critical", 502: "critical", 22: "major", 79: "major", 918: "major", 90: "major", 643: "major",
               601: "minor", 501: "minor"}


def sink_args(cwe: int, name: str, recv: str, argc: int, recv_type: str | None = None) -> list[int] | None:
    base = recv.split(".")[-1] if recv else ""
    if cwe == 89:
        if name == "<new>" and recv in SQL_COMMANDS and argc >= 1:
            return [0]
        if name in SQL_METHODS and argc >= 1 and (name not in ("Query", "Execute", "QueryAsync", "ExecuteAsync", "QueryFirst", "QueryFirstOrDefault", "QuerySingle", "QuerySingleOrDefault")
                                                  or any(h in recv.lower() for h in ("conn", "db", "sql", "dapper", "context", "session"))):
            return [0]
        return None
    if cwe == 78:
        if name == "Start" and base == "Process" and argc >= 1:
            return list(range(min(argc, 2)))
        if name == "<new>" and recv == "ProcessStartInfo" and argc >= 1:
            return list(range(min(argc, 2)))
        return None
    if cwe in (22, 23, 36, 73):
        if name == "<new>" and recv in FILE_CTORS and argc >= 1:
            return [0]
        if base in ("File", "Directory") and name in FILE_STATICS and argc >= 1:
            return [0, 1] if name in ("Copy", "Move", "Replace") and argc >= 2 else [0]
        if name in ("WriteFile", "TransmitFile") and base == "Response" and argc >= 1:
            return [0]
        if name in ("PhysicalFile", "File") and not recv and argc >= 1:
            return [0]
        return None
    if cwe == 79:
        if name == "Write" and base == "Response" and argc >= 1:
            return [0]
        if name == "Raw" and base in ("Html", "Html_x") and argc >= 1:
            return [0]
        if name == "<new>" and recv in ("HtmlString", "MvcHtmlString") and argc >= 1:
            return [0]
        if name == "Create" and recv.endswith("MvcHtmlString") and argc >= 1:
            return [0]
        if name == "Content" and not recv and argc >= 2:
            return [0]
        return None
    if cwe == 601:
        if name in ("Redirect", "RedirectPermanent") and (not recv or base == "Response") and argc >= 1:
            return [0]
        if name == "<new>" and recv == "RedirectResult" and argc >= 1:
            return [0]
        return None
    if cwe == 918:
        if name in HTTP_CLIENT_METHODS and argc >= 1 and any(h in recv.lower() for h in ("client", "webclient", "http", "wc", "request")):
            return [0]
        if name == "Create" and base in ("WebRequest", "HttpWebRequest", "FtpWebRequest") and argc >= 1:
            return [0]
        return None
    if cwe == 502:
        if name in ("Deserialize", "UnsafeDeserialize") and argc >= 1 and (recv_type in DESER_TYPES or any(t.lower() in recv.lower() for t in ("binaryformatter", "formatter", "netdatacontract"))):
            return [0]
        return None
    if cwe == 90:
        if name == "<new>" and recv == "DirectorySearcher" and argc >= 1:
            return [argc - 1] if argc <= 2 and False else [0]
        return None
    if cwe == 643:
        if name in ("SelectNodes", "SelectSingleNode", "Evaluate", "Compile") and argc >= 1:
            return [0]
        return None
    if cwe == 94:
        if name in ("CompileAssemblyFromSource", "CompileAssemblyFromFile") and argc >= 1:
            return [argc - 1]
        if name in ("Load", "LoadFrom", "LoadFile") and base == "Assembly" and argc >= 1:
            return [0]
        if name == "GetType" and base == "Type" and argc >= 1:
            return [0]
        return None
    return None


# Weakness classes shared by every language (template injection, regex injection, reflection, expression
# language, NoSQL), from extra_sinks.
from aletheore.flow import extra_sinks as _X  # noqa: E402

_X.ADDED["csharp"] = {c for c in _X.new_classes("csharp") if c not in DISCOVER_CWES_CS}
DISCOVER_CWES_CS = DISCOVER_CWES_CS + tuple(c for c in _X.new_classes("csharp") if c not in DISCOVER_CWES_CS)
for _c in _X.new_classes("csharp"):
    CWE_NAMES_CS.setdefault(_c, _X.CLASS_NAMES[_c])
    SEVERITY_CS.setdefault(_c, _X.CLASS_SEVERITY[_c])
    SANITIZERS.setdefault(_c, set())
