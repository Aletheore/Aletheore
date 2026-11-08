"""Sources, sinks and sanitizers for Rust discover and validate modes (actix-web, axum, rocket, warp, rusqlite, sqlx)."""

from __future__ import annotations

EXTRACTORS = {"Query", "Path", "Json", "Form", "Bytes", "Multipart", "Payload", "RawQuery", "TypedHeader", "Host", "Cookie", "CookieJar", "Header", "Data_x", "MatchedPath", "Uri", "Request"}
REQUEST_TYPES = {"HttpRequest", "Request", "ServiceRequest", "RequestHead", "Parts", "HttpRequest_x"}
ROUTE_ATTRS = ("#[get", "#[post", "#[put", "#[delete", "#[patch", "#[head", "#[route", "#[options", "#[rocket::get", "#[rocket::post", "#[handler", "#[web::get", "#[actix_web::get", "#[actix_web::post")
SAFE_PARAM_TYPES = {"i8", "i16", "i32", "i64", "i128", "isize", "u8", "u16", "u32", "u64", "u128", "usize", "bool", "f32", "f64", "char", "Uuid"}
REQUEST_BENIGN = {"method", "version", "peer_addr", "connection_info_x", "app_data", "extensions", "head", "match_pattern", "app_config", "cookie_x"}

SANITIZERS: dict[int, set[str]] = {
    79: {"encode_text", "encode_safe", "encode_attribute", "escape_html", "html_escape", "escape", "clean", "encode_minimal", "encode_attribute_minimal", "sanitize_html", "escape_default_x"},
    89: set(),
    78: {"quote", "shell_escape", "escape_x"},
    22: {"file_name", "sanitize", "sanitize_filename", "sanitize_with_options"},
    601: set(),
}
SAFE_METHODS = {"len", "is_empty", "contains", "starts_with", "ends_with", "eq", "ne", "parse", "is_some", "is_none", "is_ok", "is_err", "count", "to_digit", "is_numeric",
                "is_alphanumeric", "is_ascii_digit", "is_ascii_alphanumeric", "chars_count_x", "abs", "min", "max", "pow", "as_secs", "elapsed", "contains_key", "capacity",
                "is_file", "is_dir", "exists", "to_bits", "hash", "cmp", "partial_cmp", "eq_ignore_ascii_case", "find_x", "is_char_boundary"}
PASS_METHODS = {"clone", "to_string", "to_owned", "as_str", "as_ref", "as_mut", "unwrap", "expect", "unwrap_or_default", "into", "trim", "trim_start", "trim_end", "to_lowercase",
                "to_uppercase", "to_ascii_lowercase", "to_ascii_uppercase", "borrow", "deref", "as_bytes", "to_vec", "into_inner", "into_string", "to_str", "to_string_lossy", "display",
                "cloned", "copied", "as_deref", "ok", "unwrap_or_else", "ok_or", "ok_or_else", "map_err", "into_owned", "as_path", "to_path_buf", "as_os_str", "get_ref", "lock", "read", "write",
                "trim_matches", "trim_start_matches", "trim_end_matches", "strip_prefix", "strip_suffix", "join", "concat", "repeat", "replace", "replacen", "split_whitespace_x", "first", "last", "iter", "into_iter", "collect"}
DISCOVER_CWES_RS = (89, 78, 22, 79, 601, 918, 94)
CWE_NAMES_RS = {
    89: ("sql-injection", "SQL injection"),
    78: ("command-injection", "OS command injection"),
    22: ("path-traversal", "path traversal"),
    79: ("xss", "cross-site scripting"),
    601: ("open-redirect", "open redirect"),
    918: ("ssrf", "server-side request forgery"),
    94: ("code-injection", "code injection"),
}
SEVERITY_RS = {89: "critical", 78: "critical", 94: "critical", 22: "major", 79: "major", 918: "major", 601: "minor"}
_DB_HINTS = ("conn", "client", "db", "pool", "tx", "transaction", "stmt", "sql", "connection", "cursor", "sqlx", "diesel", "database", "session", "executor", "store")


def sink_args(cwe: int, name: str, recv: str, argc: int) -> list[int] | None:
    """Positions of the sink's judged arguments, None when this is not a sink for `cwe`. `recv` is the receiver or path text."""
    r = recv.lower()
    base = recv.split("::")[-1].split(".")[-1] if recv else ""
    if cwe == 89:
        if name in ("execute", "query", "query_row", "query_map", "query_one", "query_opt", "execute_batch", "prepare", "prepare_cached", "simple_query", "batch_execute", "fetch_one",
                    "fetch_all", "fetch_optional", "fetch", "raw_sql", "query_as", "query_scalar", "sql_query", "exec", "exec_drop", "exec_first", "query_iter", "query_first", "query_drop",
                    "query_raw", "execute_raw", "run_script_x") and argc >= 1:
            if name in ("sql_query", "raw_sql"):
                return [0]
            if name in ("query", "query_as", "query_scalar") and ("sqlx" in r) and not recv.endswith("!"):
                return [0]
            return [0] if any(h in r for h in _DB_HINTS) else None
        return None
    if cwe == 78:
        if name == "new" and base == "Command" and argc >= 1:
            return [0]
        if name in ("arg", "args") and argc >= 1 and any(s in recv for s in ('"-c"', '"/C"', '"/c"', '"-lc"', "'-c'")):
            return [0]
        return None
    if cwe in (22, 23, 36, 73):
        if base == "File" and name in ("open", "create", "options", "create_new", "open_buffered") and argc >= 1:
            return [0]
        if base in ("NamedFile", "ServeFile") and name in ("open", "from_path", "open_async") and argc >= 1:
            return [0]
        if base == "fs" and name in ("read_to_string", "read", "write", "remove_file", "remove_dir", "remove_dir_all", "create_dir", "create_dir_all", "copy", "rename", "read_dir",
                                       "metadata", "canonicalize", "hard_link", "symlink", "set_permissions", "File", "OpenOptions") and argc >= 1:
            return [0, 1] if name in ("copy", "rename", "hard_link", "symlink") and argc >= 2 else [0]
        if name == "open" and argc >= 1 and any(h in r for h in ("openoptions", "options", "opts")):
            return [0]
        return None
    if cwe == 79:
        if name == "Html" and not recv and argc >= 1:
            return [0]
        if base in ("Html", "RawHtml", "content") and name in ("Html", "from", "new") and argc >= 1:
            return [0]
        if name == "body" and argc >= 1 and "text/html" in recv:
            return [0]
        return None
    if cwe == 601:
        if base == "Redirect" and name in ("to", "temporary", "permanent", "found", "see_other", "moved", "to_x") and argc >= 1:
            return [0]
        if name in ("append_header", "insert_header", "header") and False:
            return None
        return None
    if cwe == 918:
        if base in ("reqwest", "ureq", "surf", "isahc", "attohttpc", "minreq", "curl") and name in ("get", "post", "put", "delete", "head", "patch", "request", "execute") and argc >= 1:
            return [0]
        if name in ("get", "post", "put", "delete", "head", "patch") and argc >= 1 and any(h in r for h in ("client", "http")) and "reqwest" not in recv.split(".")[-1]:
            return [0]
        if name == "request" and argc >= 2 and "client" in r:
            return [1]
        return None
    if cwe == 94:
        if base == "Library" and name in ("new", "open") and argc >= 1:
            return [0]
        if name in ("eval", "eval_expression", "load", "exec", "eval_with_scope", "run", "do_string") and argc >= 1 and any(h in r for h in ("engine", "lua", "rhai", "script", "interp", "context", "js")):
            return [0]
        return None
    return None
