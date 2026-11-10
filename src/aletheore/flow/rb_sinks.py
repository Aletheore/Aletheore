"""Sources, sinks and sanitizers for Ruby discover and validate modes (Rails, Sinatra, plain Ruby)."""

from __future__ import annotations

REQUEST_NAMES = {"params", "cookies"}
REQUEST_OBJ_NAMES = {"request", "req"}
CONTROLLER_BASE_SUFFIXES = ("Controller", "Base_x")
SINATRA_VERBS = {"get", "post", "put", "delete", "patch", "options", "head", "before", "after"}

SANITIZERS: dict[int, set[str]] = {
    79: {"h", "html_escape", "escape_html", "escapeHTML", "sanitize", "strip_tags", "escape_once", "simple_format", "json_escape", "escape_javascript", "j",
         "url_encode", "escape_xml", "xml_escape", "ERB_x"},
    89: {"sanitize_sql", "sanitize_sql_array", "sanitize_sql_for_conditions", "sanitize_sql_for_assignment", "sanitize_sql_like", "sanitize_sql_for_order",
         "quote", "quote_string", "quote_column_name", "quote_table_name", "connection_quote_x"},
    78: {"shellescape", "shelljoin", "shell_escape"},
    22: {"basename", "sanitize_filename"},
    601: set(),
}
SAFE_METHODS = {"to_i", "to_f", "to_r", "to_c", "to_d", "size", "length", "count", "empty?", "nil?", "blank?", "present?", "include?", "start_with?", "end_with?",
                "match?", "is_a?", "kind_of?", "instance_of?", "respond_to?", "hash", "object_id", "between?", "zero?", "positive?", "negative?", "even?", "odd?",
                "ord", "bytesize", "eql?", "equal?", "casecmp?", "integer?", "any?", "all?", "none?", "key?", "has_key?", "member?", "frozen?", "nonzero?", "!",
                "Integer", "Float", "Rational", "now", "to_time_x", "rand", "random_number", "hex", "uuid", "sample_x", "present", "numeric?", "valid?", "exists?"}
# Receivers that are pure with respect to taint: they never execute anything.
PURE_RECEIVERS = {"File", "Pathname", "Time", "Date", "DateTime", "Integer", "Float", "Math", "SecureRandom", "Digest", "Base64", "Rails", "ENV", "Regexp",
                  "String", "Array", "Hash", "Comparable", "Kernel", "JSON", "CGI", "URI", "ERB", "Shellwords", "Rack", "Process", "ActiveSupport"}
# Class-level finder methods return stored records, not request data.
DB_FINDERS = {"find", "find_by", "find_by!", "where", "find_or_create_by", "find_or_initialize_by", "first", "last", "all", "create", "create!", "new_x", "take",
              "find_each", "pluck", "select_x", "order", "limit", "joins", "includes", "left_joins", "group", "having", "distinct", "unscoped", "exists?", "sum",
              "maximum", "minimum", "average", "find_by_sql", "find_by_id", "find_by_email", "find_by_name", "find_by_username"}

DISCOVER_CWES_RB = (89, 78, 22, 79, 601, 918, 502, 94, 643, 90, 501)
CWE_NAMES_RB = {
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
SEVERITY_RB = {89: "critical", 78: "critical", 94: "critical", 502: "critical", 22: "major", 79: "major", 918: "major", 643: "major", 90: "major",
               601: "minor", 501: "minor"}

_SQL_METHODS = {"where", "find_by_sql", "exec_query", "execute", "select_all", "select_one", "select_value", "select_values", "select_rows", "select_x", "order",
                "reorder", "group", "having", "joins", "from", "pluck", "delete_all", "update_all", "exists?", "find_by", "not", "calculate", "sum", "count_x",
                "lock", "exec_update", "exec_delete", "exec_insert", "rewhere", "or", "left_joins", "left_outer_joins", "includes_x", "annotate_x", "update_counters_x"}
_SQL_RECV_ONLY = {"execute", "exec_query", "select_all", "select_one", "select_value", "select_values", "select_rows", "exec_update", "exec_delete", "exec_insert"}
_FILE_METHODS = {"read", "open", "new", "write", "delete", "readlines", "foreach", "binread", "binwrite", "rename", "unlink", "chmod", "chown", "truncate", "copy_x",
                 "readlink", "symlink", "link", "ftype", "stat_x", "lstat_x", "utime", "mkfifo"}
_FILEUTILS = {"rm", "rm_f", "rm_r", "rm_rf", "remove", "remove_file", "remove_dir", "remove_entry", "cp", "cp_r", "copy", "copy_file", "mv", "move", "mkdir",
              "mkdir_p", "makedirs", "touch", "chmod", "chmod_R", "ln", "ln_s", "install", "rmdir", "rmtree_x"}
_HTTP = {"get", "get_response", "post", "post_form", "start", "head", "put", "delete", "patch", "request", "execute", "open", "new", "get_print", "post_x"}
_HTTP_RECV = {"Net::HTTP", "HTTParty", "RestClient", "Faraday", "Typhoeus", "Excon", "URI", "OpenURI", "HTTP", "Curl::Easy", "Curl", "Mechanize", "Unirest", "Patron",
              "RestClient::Request", "Net::HTTP::Get", "Net::HTTP::Post", "Open3_x"}


def sink_args(cwe: int, name: str, recv: str, argc: int, first_const: bool = False) -> list[int] | None:
    """Positions of the sink's judged arguments, [-1] for the receiver itself, None when this is not a sink for `cwe`."""
    base = recv.split("::")[-1].split(".")[-1] if recv else ""
    if cwe == 89:
        if any(h in recv.lower() for h in ("redis", "cache", "storage", "file", "dir", "path", "arel")):
            return None
        if name in _SQL_METHODS and name not in ("not", "calculate", "sum") and (argc >= 1) and (name not in _SQL_RECV_ONLY or recv):
            return [0]
        if name in ("not", "calculate", "sum") and argc >= 1 and recv:
            return [0]
        return None
    if cwe == 78:
        if not recv and name in ("system", "exec", "spawn", "`", "<subshell>") and (argc >= 1 or name == "<subshell>"):
            return None if argc >= 2 and first_const else [0]
        if base == "Kernel" and name in ("system", "exec", "spawn", "`", "open") and argc >= 1:
            return None if argc >= 2 and first_const and name != "open" else [0]
        if base in ("Open3",) and name in ("capture2", "capture2e", "capture3", "popen2", "popen2e", "popen3", "pipeline", "pipeline_r", "pipeline_w", "pipeline_rw", "pipeline_start") and argc >= 1:
            return None if argc >= 2 and first_const else [0]
        if base == "IO" and name == "popen" and argc >= 1:
            return None if argc >= 2 and first_const else [0]
        if base in ("Process", "PTY") and name in ("spawn", "exec") and argc >= 1:
            return None if argc >= 2 and first_const else [0]
        if not recv and name == "open" and argc >= 1:
            return [0]
        return None
    if cwe in (22, 23, 36, 73):
        if base in ("File", "IO", "Dir", "Pathname", "Tempfile", "FileTest") and (name in _FILE_METHODS or name in ("glob", "entries", "children", "mkdir", "rmdir", "[]", "each_child", "exist?", "file?", "directory?", "size", "foreach", "ftype")) and argc >= 1:
            if name in ("exist?", "file?", "directory?", "size"):
                return None
            return [0, 1] if name in ("rename", "symlink", "link") and argc >= 2 else [0]
        if base in ("FileUtils", "FileUtils::Verbose") and name in _FILEUTILS and argc >= 1:
            return [0, 1] if name in ("cp", "cp_r", "copy", "copy_file", "mv", "move", "ln", "ln_s", "install") and argc >= 2 else [0]
        if not recv and name in ("send_file", "send_x", "render_file_x") and argc >= 1:
            return [0]
        if not recv and name == "open" and argc >= 1:
            return [0]
        return None
    if cwe == 79:
        if name in ("html_safe",) and not argc:
            return [-1]
        if not recv and name == "raw" and argc >= 1:
            return [0]
        return None
    if cwe == 601:
        if not recv and name in ("redirect_to", "redirect", "redirect_back_x") and argc >= 1:
            return [0]
        if base in ("Response", "Rack::Response") and name == "redirect" and argc >= 1:
            return [0]
        return None
    if cwe == 918:
        if (recv in _HTTP_RECV or base in _HTTP_RECV) and name in _HTTP and argc >= 1:
            return [0]
        if base == "URI" and name == "open" and argc >= 1:
            return [0]
        if base in ("Net::HTTP", "HTTP") and name == "new" and argc >= 1:
            return [0]
        return None
    if cwe == 502:
        if base in ("Marshal",) and name in ("load", "restore") and argc >= 1:
            return [0]
        if base in ("YAML", "Psych") and name in ("load", "unsafe_load", "load_stream", "unsafe_load_file", "load_file", "parse", "unsafe_load_stream") and argc >= 1:
            return [0]
        if base in ("Oj", "Ox") and name in ("load", "object_load", "load_file") and argc >= 1:
            return [0]
        return None
    if cwe == 94:
        if name in ("eval", "instance_eval", "class_eval", "module_eval", "instance_exec_x") and argc >= 1:
            return [0]
        if name in ("send", "public_send", "__send__", "const_get", "instance_variable_get", "instance_variable_set", "method", "public_method", "define_method", "remove_method_x") and argc >= 1:
            return [0]
        if name in ("constantize", "safe_constantize") and not argc:
            return [-1]
        if base == "ERB" and name == "new" and argc >= 1:
            return [0]
        if base == "Class" and name == "new" and False:
            return None
        return None
    if cwe == 643:
        if name in ("xpath", "at_xpath", "search_x") and argc >= 1:
            return [0]
        if base == "XPath" and name in ("first", "match", "each") and argc >= 2:
            return [1]
        return None
    if cwe == 90:
        if name in ("construct",) and "Filter" in recv and argc >= 1:
            return [0]
        return None
    return None


# Weakness classes shared by every language (template injection, regex injection, reflection, expression
# language, NoSQL), from extra_sinks.
from aletheore.flow import extra_sinks as _X  # noqa: E402

_X.ADDED["ruby"] = {c for c in _X.new_classes("ruby") if c not in DISCOVER_CWES_RB}
DISCOVER_CWES_RB = DISCOVER_CWES_RB + tuple(c for c in _X.new_classes("ruby") if c not in DISCOVER_CWES_RB)
for _c in _X.new_classes("ruby"):
    CWE_NAMES_RB.setdefault(_c, _X.CLASS_NAMES[_c])
    SEVERITY_RB.setdefault(_c, _X.CLASS_SEVERITY[_c])
    SANITIZERS.setdefault(_c, set())
