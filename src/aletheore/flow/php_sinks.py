"""Sources, sinks and sanitizers for PHP discover and validate modes."""

from __future__ import annotations

SUPERGLOBALS = {"_GET", "_POST", "_REQUEST", "_COOKIE", "_FILES"}
SERVER_TAINTED_PREFIXES = ("HTTP_", "QUERY_STRING", "REQUEST_URI", "PATH_INFO", "PHP_SELF", "PATH_TRANSLATED", "ORIG_PATH_INFO", "argv", "REDIRECT_")

# Functions whose result is a number, a hash or another value an attacker cannot shape.
SAFE_FUNCS = {
    "intval", "floatval", "doubleval", "boolval", "count", "sizeof", "strlen", "mb_strlen", "md5", "sha1", "crc32", "hash",
    "hash_hmac", "password_hash", "uniqid", "rand", "mt_rand", "random_int", "bin2hex", "random_bytes", "time", "microtime", "date",
    "gmdate", "strtotime", "mktime", "abs", "ceil", "floor", "round", "max", "min", "is_numeric", "is_int", "is_string", "is_array",
    "is_null", "is_bool", "is_float", "isset", "empty", "ctype_digit", "ctype_alnum", "ctype_alpha", "ctype_xdigit", "in_array",
    "array_key_exists", "strcmp", "strcasecmp", "strncmp", "str_contains", "str_starts_with", "str_ends_with", "preg_match",
    "preg_match_all", "version_compare", "php_uname", "phpversion", "getmypid", "spl_object_id", "urlencode", "rawurlencode",
    "base64_encode", "crc32", "ord", "chr", "dechex", "hexdec", "decbin", "bindec", "number_format", "is_file", "is_dir",
    "file_exists", "is_readable", "is_writable", "filesize", "filemtime", "function_exists", "class_exists", "defined", "mb_strpos",
    "strpos", "stripos", "strrpos", "substr_count", "array_sum", "array_product", "levenshtein", "similar_text", "dirname_safe",
}
# Sanitizers, per weakness class.
SANITIZERS: dict[int, set[str]] = {
    79: {"htmlspecialchars", "htmlentities", "esc_html", "esc_attr", "esc_url", "esc_js", "wp_kses", "wp_kses_post", "strip_tags", "e",
         "filter_var_sanitize_special_chars"},
    89: {"mysqli_real_escape_string", "mysql_real_escape_string", "real_escape_string", "escape_string", "pg_escape_string", "pg_escape_literal",
         "sqlite_escape_string", "addslashes", "quote", "escapeString"},
    78: {"escapeshellarg", "escapeshellcmd"},
    22: {"basename", "realpath", "sanitize_file_name", "wp_basename", "pathinfo"},
    98: {"basename", "realpath"},
    90: {"ldap_escape"},
    643: set(),
    94: set(),
    502: set(),
    601: {"wp_validate_redirect", "esc_url", "filter_var_url"},
    918: {"filter_var_url"},
    501: set(),
}
CAST_SAFE = {"int", "integer", "float", "double", "bool", "boolean"}
# Helpers named by convention for escaping, which projects define themselves (phpMyAdmin's quoteString and
# backquote, a framework's esc_html, a template engine's e() or h()). Matched by name, per weakness class,
# because the helper is often reached through a property whose class cannot be resolved.
import re as _re

NAME_SANITIZERS: dict[int, "_re.Pattern[str]"] = {
    89: _re.compile(r"^(quote|backquote|escape|qstr|dbquote|sqlquote)\w*$"),
    79: _re.compile(r"^(esc_|escape|htmlescape|html_escape|encode_?html|sanitize|purify|xss_)\w*$|^(e|h)$"),
    78: _re.compile(r"^(shellescape|escapeshell)\w*$"),
    22: _re.compile(r"^(safe_?filename|sanitize_?file|sanitizefilename)\w*$"),
}

SQL_FUNCS_ARG1 = {"mysqli_query", "mysqli_real_query", "mysqli_multi_query", "mysqli_send_query", "pg_query", "pg_send_query", "sqlite_query",
                  "mysqli_prepare", "mssql_query", "odbc_exec", "odbc_prepare", "oci_parse", "sqlsrv_query", "sqlsrv_prepare", "pg_prepare_x"}
SQL_FUNCS_ARG0 = {"mysql_query", "mysql_unbuffered_query", "sqlite_exec", "sqlite_array_query", "pg_query_params_x", "db_query", "mysql_db_query_x"}
SQL_METHODS = {"query", "exec", "prepare", "real_query", "multi_query", "execute_query", "get_results", "get_var", "get_row", "get_col",
               "raw", "select", "statement", "unprepared", "whereRaw", "selectRaw", "orderByRaw", "havingRaw", "execute"}
SQL_RECV_HINTS = ("db", "pdo", "mysqli", "conn", "link", "wpdb", "database", "sql", "dbh", "query", "model", "stmt")
CMD_FUNCS = {"exec", "shell_exec", "system", "passthru", "popen", "proc_open", "pcntl_exec"}
FILE_ARG0 = {"fopen", "file_get_contents", "file_put_contents", "readfile", "file", "unlink", "mkdir", "rmdir", "opendir", "scandir", "glob",
             "touch", "chmod", "chown", "readlink", "parse_ini_file", "highlight_file", "show_source", "php_strip_whitespace", "fileperms",
             "is_uploaded_file_x", "imagecreatefromjpeg", "imagecreatefrompng", "imagecreatefromgif", "simplexml_load_file", "md5_file", "sha1_file",
             "readgzfile", "gzopen", "fpassthru_x", "tempnam_x", "rename", "copy", "symlink", "link", "ftp_get", "zip_open"}
FILE_BOTH = {"rename", "copy", "symlink", "link"}
INCLUDE_KINDS = ("include_expression", "include_once_expression", "require_expression", "require_once_expression")

DISCOVER_CWES_PHP = (89, 78, 22, 79, 98, 94, 502, 918, 601, 90, 643, 501)
CWE_NAMES_PHP = {
    89: ("sql-injection", "SQL injection"),
    78: ("command-injection", "OS command injection"),
    22: ("path-traversal", "path traversal"),
    79: ("xss", "cross-site scripting"),
    98: ("file-inclusion", "file inclusion"),
    94: ("code-injection", "code injection"),
    502: ("insecure-deserialization", "insecure deserialization"),
    918: ("ssrf", "server-side request forgery"),
    601: ("open-redirect", "open redirect"),
    90: ("ldap-injection", "LDAP injection"),
    643: ("xpath-injection", "XPath injection"),
    501: ("trust-boundary", "trust boundary violation"),
}
SEVERITY_PHP = {89: "critical", 78: "critical", 94: "critical", 98: "critical", 502: "critical", 22: "major", 79: "major", 918: "major",
                90: "major", 643: "major", 601: "minor", 501: "minor"}


def sink_args(cwe: int, name: str, recv: str, argc: int, recv_type: str | None = None, first_const: str | None = None) -> list[int] | None:
    n = name.lower()
    low = recv.lower()
    if cwe == 89:
        if n in SQL_FUNCS_ARG1 and argc >= 2:
            return [1]
        if n in SQL_FUNCS_ARG0 and argc >= 1:
            return [0]
        if recv and n in {m.lower() for m in SQL_METHODS} and argc >= 1 and (recv_type in ("PDO", "mysqli", "wpdb", "Connection", "DB") or any(h in low for h in SQL_RECV_HINTS)):
            return [0]
        return None
    if cwe == 78:
        if not recv and n in CMD_FUNCS and argc >= 1:
            return [0]
        return None
    if cwe in (22, 23, 36, 73):
        if not recv and n in FILE_ARG0 and argc >= 1:
            return [0, 1] if n in FILE_BOTH and argc >= 2 else [0]
        if not recv and n == "move_uploaded_file" and argc >= 2:
            return [1]
        return None
    if cwe == 98:
        return None  # include/require are handled as their own node type
    if cwe == 94:
        if not recv and n in ("eval", "assert", "create_function", "call_user_func", "call_user_func_array", "preg_replace_callback_x") and argc >= 1:
            return [0]
        return None
    if cwe == 502:
        if not recv and n in ("unserialize", "yaml_parse", "yaml_parse_file") and argc >= 1:
            return [0]
        return None
    if cwe == 918:
        if not recv and n in ("curl_init", "file_get_contents", "fopen", "fsockopen", "get_headers", "simplexml_load_file", "curl_setopt_x") and argc >= 1:
            return [0]
        if not recv and n == "curl_setopt" and argc >= 3 and first_const == "CURLOPT_URL":
            return [2]
        return None
    if cwe == 601:
        if not recv and n == "header" and argc >= 1:
            return [0]
        return None
    if cwe == 90:
        if not recv and n in ("ldap_search", "ldap_list", "ldap_read") and argc >= 3:
            return [1, 2]
        return None
    if cwe == 643:
        if recv and n in ("xpath", "evaluate", "query") and argc >= 1 and (recv_type in ("DOMXPath", "SimpleXMLElement") or n == "xpath"):
            return [0]
        return None
    return None
