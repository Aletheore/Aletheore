"""Sink catalogue shared by every language: weakness classes added on top of each language's own sink
table, and extra APIs for classes a language already reports (ORM and query-builder raw-SQL methods).

Each language's `sink_args` asks `sink_args(LANG, cwe, name, recv, argc)` first. Calls are normalised
before matching: a constructor is matched by its class name with no receiver (`new Regex(x)`,
`Regex(x)`, `ERB.new(x)` is matched as name `new` on receiver `ERB`).

A rule is (method names, receiver rule, argument positions). Receiver rules:
- ANY: any receiver or none;  UNQ: no receiver (a plain function or a constructor);
- ("base", {...}): the last segment of the receiver (`a.b.Template` -> `Template`, `Engine.Razor` -> `Razor`);
- ("root", {...}): the first segment (`template.New("x")` -> `template`);
- ("has", {...}): the lower-cased receiver text contains one of the strings.
Positions are argument indexes that must not carry attacker data. Every rule here fires only on a string
taken from or built from request data, never on a container: `User.where(name: params[:n])` passes a hash
of bound conditions and is safe, `User.where("name = '#{params[:n]}'")` is not.

Sanitizers here are qualified (`re.escape`), so `html.escape` does not count as a regex escape.
"""

from __future__ import annotations

from functools import lru_cache

import re

ANY = ("any",)
UNQ = ("unq",)
RECV = ("recv",)   # any receiver, but not a plain function call
INST = ("inst",)   # a receiver that is a variable or expression, not a class (`s.split(x)`, not `StringUtils.split(x)`)


def base(*names: str):
    return ("base", frozenset(names))


def root(*names: str):
    return ("root", frozenset(names))


def has(*subs: str):
    return ("has", tuple(s.lower() for s in subs))


def exact(*texts: str):
    return ("exact", frozenset(texts))


def either(*rules):
    return ("or", rules)


# New weakness classes: slug, label and severity, shared by every language.
CLASS_NAMES = {
    1336: ("template-injection", "server-side template injection"),
    942: ("cors-origin-reflection", "CORS origin reflection"),
    88: ("argument-injection", "argument injection"),
    915: ("mass-assignment", "mass assignment"),
    1321: ("prototype-pollution", "prototype pollution"),
    643: ("xpath-injection", "XPath injection"),
    1333: ("regex-injection", "regular expression injection (ReDoS)"),
    470: ("unsafe-reflection", "unsafe reflection"),
    917: ("expression-injection", "expression language injection"),
    943: ("nosql-injection", "NoSQL injection"),
}
CLASS_SEVERITY = {643: "major", 915: "major", 1321: "major", 88: "major", 1336: "critical", 917: "critical", 943: "critical", 470: "major", 1333: "minor", 942: "minor"}


def _const(v) -> str | None:
    val = getattr(v, "value", None)
    return val if type(v).__name__ == "Const" and isinstance(val, str) else None


def _acao(args) -> bool:
    """The header being set is Access-Control-Allow-Origin."""
    c = _const(args[0]) if args else None
    return c is not None and c.strip().lower() == "access-control-allow-origin"


def _acao_line(args) -> bool:
    """PHP header("Access-Control-Allow-Origin: " . $origin): the constant text before the request value names the header."""
    if not args:
        return False
    alts = getattr(args[0], "alts", None) or (args[0],)
    return any((getattr(a, "prefix", "") or "").strip().lower().startswith("access-control-allow-origin") for a in alts)


# Programs that read a leading "-" in any argument as an option, some of which run commands or write files
# (git --upload-pack, ssh -oProxyCommand, curl -o / -K, tar --to-command, find -exec, rsync -e, interpreters -c / -e).
DANGEROUS_PROGRAMS = {"git", "ssh", "scp", "sftp", "rsync", "curl", "wget", "tar", "zip", "unzip", "7z", "7za", "find", "xargs",
                      "sendmail", "mail", "mutt", "psql", "mysql", "mysqldump", "pg_dump", "ffmpeg", "convert", "magick", "gpg", "hg",
                      "svn", "docker", "kubectl", "npm", "npx", "pip", "python", "python3", "node", "perl", "ruby", "php", "env",
                      "awk", "sed", "less", "man", "openssl", "aws", "gcloud", "gs", "ghostscript", "exiftool", "wkhtmltopdf"}
_VARARGS = list(range(1, 12))
_ARGV0 = {"CommandContext"}  # Go exec.CommandContext(ctx, program, args...): the program is the second argument

# `new Model(req.body)`: any constructor except the language's own value types.
CAP = "<capitalised>"
_JS_BUILTINS = {"String", "Number", "Boolean", "Object", "Array", "Date", "Error", "TypeError", "RangeError", "RegExp", "Promise", "Map",
                "Set", "WeakMap", "WeakSet", "URL", "URLSearchParams", "Buffer", "Symbol", "BigInt", "JSON", "Uint8Array", "Blob",
                "Response", "Request", "Headers", "FormData", "Intl", "Proxy", "Reflect"}

_CORS_HEADER = [({"setHeader", "addHeader", "header", "set", "append", "Set", "Add", "Append", "TryAdd", "add"}, ANY, [1], _acao)]

_SQL_DB = has("db", "conn", "pool", "client", "database", "cursor", "session", "tx", "trans", "repo", "store", "sql", "orm",
              "knex", "sequelize", "prisma", "manager", "entitymanager", "queryrunner", "datasource")
_QB = has("querybuilder", "createquerybuilder", "qb")

RULES: dict[str, dict[int, list[tuple]]] = {
    "python": {
        22: [({"save"}, RECV, [0]), ({"save"}, has("storage"), [0])],
        88: [({"run", "call", "Popen", "check_output", "check_call"}, either(root("subprocess"), UNQ), [0])],
        942: _CORS_HEADER,
        89: [
            ({"text", "literal_column"}, either(UNQ, root("sqlalchemy", "sa", "sql", "expression")), [0]),
            ({"RawSQL"}, either(UNQ, base("expressions", "models")), [0]),
            ({"SQL"}, either(root("sql", "psycopg", "psycopg2", "peewee", "pw"), UNQ), [0]),
            ({"read_sql", "read_sql_query"}, root("pd", "pandas"), [0]),
            ({"fetch", "fetchrow", "fetchval", "fetch_all", "fetch_one", "fetch_val", "executescript"},
             has("conn", "pool", "db", "database", "cursor", "tx", "trans"), [0]),
        ],
        1336: [
            ({"render_template_string"}, either(UNQ, root("flask")), [0]),
            ({"from_string"}, ANY, [0]),
            ({"Template"}, either(root("jinja2", "mako", "template", "django"), base("template")), [0]),
        ],
        1333: [
            ({"compile", "match", "search", "fullmatch", "sub", "subn", "split", "findall", "finditer"}, root("re", "regex"), [0]),
        ],
        470: [
            ({"import_string", "locate"}, either(UNQ, root("pydoc", "module_loading")), [0]),
        ],
        917: [],
        943: [({"find", "find_one", "find_one_and_update", "find_one_and_delete", "find_one_and_replace", "update_one", "update_many",
                "delete_one", "delete_many", "count_documents", "aggregate", "replace_one"}, RECV, [0])],
    },
    "javascript": {
        22: [({"mv"}, RECV, [0])],
        643: [({"select", "select1", "evaluate", "parse"}, root("xpath"), [0])],
        915: [({"create", "insertMany", "bulkCreate", "build", "insert", "upsert", "createMany"}, RECV, [0]),
              ({"findByIdAndUpdate", "findOneAndUpdate", "updateOne", "updateMany", "update", "replaceOne", "findOneAndReplace", "updateMany"},
               RECV, [0, 1]),
              ({"assign"}, base("Object"), [1]),
              ({"set"}, has("user", "doc", "model", "record", "account", "profile"), [0]),
              ({CAP}, UNQ, [0])],
        1321: [],
        88: [({"spawn", "spawnSync", "execFile", "execFileSync"}, either(UNQ, base("child_process", "cp", "childProcess")), [1])],
        942: _CORS_HEADER,
        89: [
            ({"$queryRawUnsafe", "$executeRawUnsafe"}, ANY, [0]),
            ({"literal"}, either(base("sequelize", "Sequelize"), root("sequelize", "Sequelize")), [0]),
            ({"whereRaw", "orWhereRaw", "andWhereRaw", "havingRaw", "orHavingRaw", "orderByRaw", "groupByRaw", "joinRaw", "fromRaw",
              "selectRaw", "whereNotRaw"}, ANY, [0]),
            ({"raw"}, either(base("sql", "Prisma", "knex", "db"), root("sql", "Prisma", "knex")), [0]),
            ({"where", "andWhere", "orWhere", "having", "andHaving", "orHaving", "orderBy", "addOrderBy", "groupBy", "addGroupBy",
              "select", "addSelect", "innerJoin", "leftJoin", "innerJoinAndSelect", "leftJoinAndSelect", "setParameter_x"}, _QB, [0]),
            ({"none", "one", "many", "any", "oneOrNone", "manyOrNone", "result", "multi"}, base("db", "tx", "pgp", "database", "t"), [0]),
        ],
        1336: [
            ({"render", "compile"}, root("ejs", "pug", "jade", "Handlebars", "handlebars", "hbs", "Mustache", "mustache", "eta", "Eta",
                                        "Sqrl", "squirrelly", "dot", "doT", "Hogan", "hogan", "swig", "Twig", "twig", "liquid", "nunjucks"), [0]),
            ({"precompile"}, root("Handlebars", "handlebars"), [0]),
            ({"renderString", "compile"}, root("nunjucks", "env", "nunjucksEnv"), [0]),
            ({"template"}, root("_", "lodash", "doT", "dot", "underscore"), [0]),
            ({"parseAndRender", "parse"}, either(root("engine", "liquid", "Liquid"), base("Liquid")), [0]),
        ],
        1333: [
            ({"RegExp"}, UNQ, [0]),
        ],
        470: [],
        917: [],
        943: [],
    },
    "php": {
        942: [({"header"}, UNQ, [0], _acao_line)],
        89: [
            ({"raw"}, base("DB", "Capsule"), [0]),
            ({"statement", "unprepared", "select", "insert", "update", "delete", "selectOne", "scalar"}, base("DB", "Capsule"), [0]),
            ({"query", "exec", "prepare", "multi_query", "real_query", "send_query"}, RECV, [0]),
            ({"executeQuery", "executeStatement", "fetchAllAssociative", "fetchAssociative", "fetchOne", "fetchAll"}, has("conn", "connection", "dbal", "db", "em"), [0]),
            ({"createQuery", "createNativeQuery"}, ANY, [0]),
        ],
        1336: [
            ({"createTemplate"}, ANY, [0]),
            ({"render"}, base("Blade"), [0]),
            ({"fetch", "display"}, has("smarty", "tpl", "view"), [0]),
        ],
        1333: [
            ({"preg_match", "preg_match_all", "preg_replace", "preg_replace_callback", "preg_split", "preg_grep", "preg_filter"}, UNQ, [0]),
        ],
        470: [
            ({"call_user_func", "call_user_func_array", "forward_static_call", "forward_static_call_array"}, UNQ, [0]),
            ({"ReflectionClass", "ReflectionMethod", "ReflectionFunction"}, UNQ, [0]),
        ],
        917: [],
        943: [],
    },
    "java": {
        22: [({"transferTo"}, RECV, [0])],
        88: [({"ProcessBuilder"}, UNQ, [0] + _VARARGS), ({"exec"}, has("runtime"), [0])],
        942: _CORS_HEADER,
        89: [
            ({"query", "queryForObject", "queryForList", "queryForMap", "queryForRowSet", "update", "batchUpdate", "queryForStream"},
             has("jdbc"), [0]),
            ({"rawQuery", "execSQL"}, ANY, [0]),
            ({"field", "condition", "sql", "query", "resultQuery", "fetch", "execute", "fetchOne", "fetchAny", "fetchLazy"},
             either(base("DSL"), has("dsl", "jooq", "create", "context")), [0]),
            ({"createSQLQuery", "createNativeQuery", "createQuery", "createSqlQuery"}, ANY, [0]),
        ],
        1336: [
            ({"evaluate"}, has("velocity"), [3]),
            ({"Template"}, UNQ, [1]),
            ({"process"}, has("templateengine", "thymeleaf", "engine"), [0]),
            ({"getLiteralTemplate"}, ANY, [0]),
            ({"createTemplate"}, has("templateengine", "simpletemplate", "groovy", "engine"), [0]),
            ({"render", "renderForResult"}, has("jinjava"), [0]),
            ({"compile"}, has("mustache", "handlebars"), [0]),
        ],
        1333: [
            ({"compile", "matches"}, base("Pattern"), [0]),
            # String's own regex methods, by their arity: `PasswordEncoder.matches(raw, encoded)` is not one of them
            ({"matches"}, INST, [0], lambda a: len(a) == 1),
            ({"replaceAll", "replaceFirst"}, INST, [0], lambda a: len(a) == 2),
            ({"split"}, INST, [0], lambda a: len(a) in (1, 2)),
        ],
        470: [
            ({"forName"}, base("Class"), [0]),
            ({"loadClass"}, RECV, [0]),
            ({"getMethod", "getDeclaredMethod"}, RECV, [0]),
        ],
        917: [
            ({"parseExpression", "parseRaw"}, has("parser", "spel", "expression"), [0]),
            ({"getValue", "setValue", "parseExpression"}, base("Ognl"), [0]),
            ({"eval", "compileExpression", "evalToString", "executeExpression"}, base("MVEL"), [0]),
            ({"createExpression", "createScript"}, has("jexl", "engine"), [0]),
            ({"eval", "getValue"}, has("elprocessor"), [0]),
            ({"createValueExpression", "createMethodExpression"}, has("factory", "expressionfactory"), [1]),
        ],
        943: [
            ({"parse"}, base("Document", "BasicDBObject", "BsonDocument"), [0]),
            ({"BasicDBObject", "Document", "BsonDocument"}, UNQ, [1]),
            ({"where"}, base("Filters"), [0]),
        ],
    },
    "csharp": {
        22: [({"ExtractToFile"}, RECV, [0]), ({"ExtractToDirectory"}, base("ZipFile"), [1])],
        88: [({"Start"}, base("Process"), [1]), ({"ProcessStartInfo"}, UNQ, [1])],
        942: _CORS_HEADER,
        89: [
            ({"Query", "QueryAsync", "QueryFirst", "QueryFirstAsync", "QueryFirstOrDefault", "QueryFirstOrDefaultAsync", "QuerySingle",
              "QuerySingleAsync", "QuerySingleOrDefault", "QuerySingleOrDefaultAsync", "QueryMultiple", "QueryMultipleAsync",
              "Execute", "ExecuteAsync", "ExecuteScalar", "ExecuteScalarAsync", "ExecuteReader", "ExecuteReaderAsync"}, _SQL_DB, [0]),
            ({"SqlQuery", "SqlQueryRaw", "ExecuteSqlCommand", "ExecuteSqlCommandAsync", "ExecuteSqlRaw", "ExecuteSqlRawAsync", "FromSqlRaw", "FromSql"},
             ANY, [0]),
            ({"CreateSQLQuery", "CreateQuery"}, ANY, [0]),
            ({"SqlCommand", "SqliteCommand", "NpgsqlCommand", "MySqlCommand", "OleDbCommand", "OdbcCommand", "SQLiteCommand", "OracleCommand"}, UNQ, [0]),
        ],
        1336: [
            ({"RunCompile", "Compile", "Parse", "IsTemplateCached_x"}, has("razor", "engine.razor"), [0]),
            ({"Parse"}, base("Template", "FluidTemplate"), [0]),
        ],
        1333: [
            ({"Regex"}, UNQ, [0]),
            ({"IsMatch", "Match", "Matches", "Replace", "Split", "EnumerateMatches", "Count"}, base("Regex"), [1]),
        ],
        470: [
            ({"GetType"}, base("Type"), [0]),
            ({"CreateInstance", "CreateInstanceFrom"}, base("Activator"), [0, 1]),
            ({"Load", "LoadFrom", "LoadFile", "UnsafeLoadFrom"}, base("Assembly"), [0]),
            ({"GetMethod", "InvokeMember"}, RECV, [0]),
        ],
        917: [],
        943: [
            ({"Parse"}, base("BsonDocument"), [0]),
            ({"BsonDocument"}, UNQ, [1]),
            ({"Find", "FindAsync", "CountDocuments", "DeleteMany", "DeleteOne", "UpdateOne", "UpdateMany"}, has("collection", "coll", "_col"), [0]),
        ],
    },
    "go": {
        88: [({"Command"}, root("exec"), _VARARGS), ({"CommandContext"}, root("exec"), _VARARGS[1:])],
        942: _CORS_HEADER,
        89: [
            ({"Not", "Or", "Distinct", "Table", "Pluck"}, has("db", "tx", "gorm", "orm", "query", "session"), [0]),
            ({"Expr"}, root("sq", "squirrel"), [0]),
            ({"L", "Literal"}, root("goqu"), [0]),
        ],
        1336: [
            ({"Parse"}, has("template", "tmpl", "tpl"), [0]),
        ],
        1333: [],
        470: [],
        917: [],
        943: [],
    },
    "ruby": {
        22: [({"extract"}, RECV, [0])],
        88: [({"system", "spawn", "exec"}, UNQ, _VARARGS), ({"capture2", "capture2e", "capture3", "popen3", "popen2", "pipeline"}, base("Open3"), _VARARGS)],
        942: [({"set_header", "add_header"}, ANY, [1], _acao)],
        89: [
            ({"where", "order", "reorder", "group", "having", "joins", "select", "pluck", "from", "lock", "find_by_sql", "count_by_sql",
              "update_all", "delete_all", "rewhere", "in_order_of_x"}, RECV, [0]),
            ({"execute", "exec_query", "select_all", "select_one", "select_value", "select_values", "select_rows", "exec_update",
              "exec_delete", "exec_insert", "query", "fetch"}, has("connection", "conn", "db", "client", "sequel"), [0]),
            ({"lit"}, base("Sequel"), [0]),
        ],
        1336: [
            ({"new"}, exact("ERB", "Erubi::Engine", "Erubis::Eruby", "Erubis::EscapedEruby", "Haml::Engine", "Haml::Template", "Slim::Template",
                            "Tilt::ERBTemplate", "Tilt::HamlTemplate", "Tilt::ErubiTemplate", "Liquid::Template_x"), [0]),
        ],
        1333: [
            ({"new", "compile", "union_x"}, base("Regexp"), [0]),
        ],
        470: [
            ({"const_get", "const_source_location", "class_variable_get"}, ANY, [0]),
            ({"send", "public_send", "__send__", "method", "instance_variable_get", "instance_variable_set", "try", "constantize_x"}, ANY, [0]),
        ],
        917: [],
        943: [],
    },
    "kotlin": {
        22: [({"transferTo"}, RECV, [0])],
        88: [({"ProcessBuilder"}, UNQ, [0] + _VARARGS), ({"exec"}, has("runtime"), [0])],
        942: _CORS_HEADER,
        89: [
            ({"query", "queryForObject", "queryForList", "queryForMap", "queryForRowSet", "update", "batchUpdate", "queryForStream"},
             has("jdbc"), [0]),
            ({"rawQuery", "execSQL"}, ANY, [0]),
            ({"exec"}, either(UNQ, has("transaction", "current()", "tx")), [0]),
            ({"SimpleSQLiteQuery"}, UNQ, [0]),
            ({"createQuery", "createNativeQuery", "createSQLQuery"}, ANY, [0]),
        ],
        1336: [
            ({"evaluate"}, has("velocity"), [3]),
            ({"Template"}, UNQ, [1]),
            ({"process"}, has("templateengine", "thymeleaf", "engine"), [0]),
            ({"getLiteralTemplate"}, ANY, [0]),
        ],
        1333: [
            ({"Regex"}, UNQ, [0]),
            ({"compile", "matches"}, base("Pattern"), [0]),
        ],
        470: [
            ({"forName"}, base("Class"), [0]),
            ({"loadClass"}, RECV, [0]),
            ({"getMethod", "getDeclaredMethod"}, RECV, [0]),
        ],
        917: [
            ({"parseExpression", "parseRaw"}, has("parser", "spel", "expression"), [0]),
            ({"getValue", "setValue", "parseExpression"}, base("Ognl"), [0]),
            ({"eval", "compileExpression"}, base("MVEL"), [0]),
        ],
        943: [
            ({"parse"}, base("Document", "BasicDBObject", "BsonDocument"), [0]),
            ({"BasicDBObject", "Document", "BsonDocument"}, UNQ, [1]),
            ({"where"}, base("Filters"), [0]),
        ],
    },
    "rust": {
        22: [({"unpack"}, RECV, [0])],
        942: [],
        89: [
            ({"from_string", "from_sql_and_values"}, base("Statement"), [1]),
            ({"sql"}, either(UNQ, root("diesel", "dsl")), [0]),
        ],
        1336: [
            ({"one_off"}, base("Tera"), [0]),
            ({"add_raw_template", "register_template_string", "add_template"}, ANY, [1]),
            ({"render_template", "render_str", "template_from_str"}, ANY, [0]),
        ],
        1333: [
            ({"new"}, has("fancy_regex"), [0]),
        ],
        470: [],
        917: [],
        943: [],
    },
}

# Qualified sanitizers for the new classes: (receiver base, method name), checked with the receiver.
SANITIZERS_Q: dict[str, dict[int, set[tuple[str, str]]]] = {
    "python": {1333: {("re", "escape"), ("regex", "escape")}},
    "javascript": {1333: {("", "escapeRegExp"), ("_", "escapeRegExp"), ("lodash", "escapeRegExp"), ("", "escapeStringRegexp"),
                          ("", "escapeRegex"), ("", "escapeRegExpString")}},
    "php": {1333: {("", "preg_quote")}},
    "java": {1333: {("Pattern", "quote")}},
    "kotlin": {1333: {("Regex", "escape"), ("Pattern", "quote")}},
    "csharp": {1333: {("Regex", "Escape")}},
    "ruby": {1333: {("Regexp", "escape"), ("Regexp", "quote")}},
    "go": {}, "rust": {1333: {("regex", "escape"), ("fancy_regex", "escape")}},
}

_PARENS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|<[^<>]*>")
_SPLIT = re.compile(r"\s*(?:\.|::|->|\?\.|&\.)\s*")


@lru_cache(maxsize=65536)
def _segments(recv: str) -> tuple[str, ...]:
    s = recv or ""
    for _ in range(6):
        t = _PARENS.sub("", s)
        if t == s:
            break
        s = t
    s = s.strip().lstrip("$@:&*!")
    return tuple(p for p in _SPLIT.split(s) if p)


def _match(rule, recv: str) -> bool:
    kind = rule[0]
    if kind == "any":
        return True
    if kind == "unq":
        return not recv
    if kind == "recv":
        return bool(recv)
    if kind == "exact":
        return (recv or "").lstrip(":") in rule[1]
    if kind == "inst":
        segs = _segments(recv)
        return bool(segs) and segs[0][:1].islower() and segs[0] not in ("this", "self")
    if kind == "or":
        return any(_match(r, recv) for r in rule[1])
    segs = _segments(recv)
    if kind == "base":
        return bool(segs) and segs[-1] in rule[1]
    if kind == "root":
        return bool(segs) and segs[0] in rule[1]
    if kind == "has":
        low = (recv or "").lower()
        return bool(low) and any(s in low for s in rule[1])
    return False


def sink_args(lang: str, cwe: int, name: str, recv: str, argc: int, args=None) -> list[int] | None:
    """Argument positions that must not carry attacker data, or None when the call is not a sink here.
    A rule with a fourth element is a condition on the argument values (the header a call sets)."""
    if "<" in name and not name.startswith("<"):
        name = name.split("<", 1)[0]  # conn.Query<User>(...): the type arguments are not part of the name
    for r in RULES.get(lang, {}).get(cwe, ()):
        names, rule, pos = r[0], r[1], r[2]
        if len(r) > 3 and not r[3](list(args or ())):
            continue
        hit = name in names or (CAP in names and name[:1].isupper() and name not in _JS_BUILTINS)
        if hit and _match(rule, recv):
            p = [i for i in pos if i < argc]
            if p:
                return p
    return None


def _prefix(v) -> str:
    alts = getattr(v, "alts", None) or (v,)
    return min(((getattr(a, "prefix", "") or "") for a in alts), key=len, default="")


def _arg_injection(lang: str, name: str, i: int, args: list, ts) -> bool:
    """A request value given as its own argument to a program that parses options, with no `--` before it and no
    constant text glued in front (which would stop it starting with "-")."""
    v = args[i]
    items = getattr(v, "items", None)
    if items is not None and type(v).__name__ == "ListV":
        if getattr(v, "dirty", False) or getattr(v, "opaque", False):
            return False  # an argv the engine cannot read element by element
        prog, rest = (items[0], items[1:]) if i == 0 else (args[0], items)
    else:
        if lang == "csharp":  # Process.Start(program, "arguments string"): the string is split on spaces
            prog, rest = args[0], [v]
        elif i == 0:
            return False  # Runtime.exec(String): one command line, OS command injection's business
        else:
            first = 1 if name in _ARGV0 else 0
            prog, rest = args[first], args[first + 1:i + 1]
    p = _const(prog)
    if p is None or p.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe") not in DANGEROUS_PROGRAMS:
        return False
    for it in rest:
        if _const(it) == "--":
            return False
        if ts(it) and (lang == "csharp" or not _prefix(it)):
            return True
    return False


def ext_hit(lang: str, cwe: int, name: str, i: int, args: list, ts, node=None) -> bool:
    """Whether the value at a shared-catalogue sink position is attacker data that reaches the sink."""
    if i >= len(args):
        return False
    if cwe == 942:
        return ts(args[i]) and not origin_checked(node)
    if cwe == 88:
        return _arg_injection(lang, name, i, args, ts)
    if cwe == 915:
        return whole_body(args[i])
    if cwe == 943:  # pymongo filters are dicts; other drivers also parse a JSON query string
        return whole_body(args[i]) or _where_clause(args[i], ts) or (lang != "python" and ts(args[i]))
    if cwe == 22:
        from aletheore.flow.values import classify  # a File or Path object built from request data, not only a string
        return ts(args[i]) or classify(args[i]) == "tainted"
    return ts(args[i])


def _where_clause(v, ts) -> bool:
    alts = getattr(v, "alts", None) or (v,)
    return any(type(a).__name__ == "MapV" and a.entries.get("$where") is not None and ts(a.entries["$where"]) for a in alts)


_BODY = {"request.body", "request.json", "request.get_json", "request.POST", "request.data", "request.form", "request.payload"}


def whole_body(v) -> bool:
    """The whole parsed request body (every field the client sent), not one value taken from it. A Prisma-style
    `{ data: req.body }` wrapper counts too."""
    alts = getattr(v, "alts", None) or (v,)
    for a in alts:
        if type(a).__name__ == "Tainted" and a.why in _BODY:
            return True
        if type(a).__name__ == "MapV" and any(whole_body(a.entries.get(k)) for k in ("data", "create", "update") if a.entries.get(k) is not None):
            return True
    return False


def acao_store(key, value, tainted_string, node=None) -> bool:
    """`headers["Access-Control-Allow-Origin"] = origin` (Python, Ruby, C#, Django `response[...]`) with a request value."""
    c = _const(key)
    return c is not None and c.strip().lower() == "access-control-allow-origin" and tainted_string(value) and not origin_checked(node)


_FN_TYPES = {"method_declaration", "function_declaration", "function_definition", "method_definition", "arrow_function",
             "function_expression", "function", "method", "singleton_method", "function_item", "func_literal", "lambda_literal",
             "local_function_statement", "constructor_declaration", "anonymous_function_creation_expression"}
_HEADER_RX = re.compile(r"""["']access-control-allow-origin:?\s*["']\s*[,\]\.\)]?""", re.I)
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_CHECK = re.compile(r"\b(if|unless|elif|elsif|guard|when)\b")
_TEST = re.compile(r"\(|\bin\b|==|!=|===|!==|=~|\binclude\?|\bincludes\b|\bcontains\b|\bmatch")
_NOISE = {"response", "res", "resp", "headers", "header", "setHeader", "addHeader", "set", "Set", "Add", "add", "append", "Append",
          "TryAdd", "w", "Header", "ctx", "c", "this", "self", "request", "req", "Response", "Headers"}


def origin_checked(node) -> bool:
    """The reflected origin was tested in an `if` earlier in the same function (an allowlist helper such as
    `if (checkURL(origin) == null) return;`, `if origin in ALLOWED`). CORS reflection is reported only when nothing
    in the function looks at the origin before echoing it back."""
    if node is None:
        return False
    if node.parent is not None and "assignment" in node.parent.type:
        node = node.parent  # headers["Access-Control-Allow-Origin"] = origin: the value is on the right
    fn = node
    while fn is not None and fn.type not in _FN_TYPES:
        fn = fn.parent
    if fn is None:
        return False
    call = node.text.decode("utf-8", "replace")
    tail = _HEADER_RX.split(call, 1)[-1]
    names = {m for m in _IDENT.findall(tail) if m not in _NOISE and not m[:1].isdigit()}
    if not names:
        return False
    before = fn.text[: max(0, node.start_byte - fn.start_byte)].decode("utf-8", "replace")
    for line in before.splitlines():
        if _CHECK.search(line) and _TEST.search(line) and any(re.search(r"\b" + re.escape(n) + r"\b", line) for n in names):
            return True
    return False


_SANITIZES: dict = {}


def sanitizes(lang: str, cwes, name: str, recv: str) -> bool:
    """A qualified sanitizer for one of the new classes (`re.escape(x)` for a regex built from x)."""
    key = (lang, name, recv, cwes if isinstance(cwes, (tuple, frozenset)) else frozenset(cwes))
    got = _SANITIZES.get(key)
    if got is None:
        table = SANITIZERS_Q.get(lang, {})
        segs = _segments(recv)
        b = segs[-1] if segs else ""
        got = _SANITIZES[key] = any((b, name) in table.get(c, ()) for c in cwes)
    return got


# Classes a driver detects with its own logic (no table rule): Python `Model(**request.json)`, Ruby `params.permit!`,
# JS prototype pollution.
CUSTOM = {"python": (915,), "ruby": (915,), "javascript": (1321,)}
CUSTOM_WORDS = {915: (b"**request", b"**self.request", b"**data", b"**payload", b"**body", b"**kwargs_x", b"permit!"),
                1321: (b"][", b" in "), 470: (b"getattr(",), 1333: (b"/#{", b"%r{")}


# Per language, the classes this catalogue ADDED (a language's own classes, such as JS NoSQL, stay core).
ADDED: dict[str, set[int]] = {}


def added(lang: str, cwe: int) -> bool:
    return cwe in ADDED.get(lang, set())


def local_ok(cwe: int, data: bytes, words: dict, lang: str = "") -> bool:
    """The added classes run only on files that contain one of their sink words: they are rarer than the core
    classes, and running each as a full pass on every request-handling file would multiply scan time."""
    return not added(lang, cwe) or any(w in data for w in words.get(cwe, ()))


def rule_classes(lang: str) -> tuple[int, ...]:
    """Every class with a shared-catalogue rule or custom detection for this language (new or existing)."""
    return tuple(sorted({c for c, r in RULES.get(lang, {}).items() if r} | set(CUSTOM.get(lang, ()))))


def new_classes(lang: str) -> tuple[int, ...]:
    """The added classes that have at least one rule, or custom detection, for this language."""
    return tuple(c for c in CLASS_NAMES if RULES.get(lang, {}).get(c) or c in CUSTOM.get(lang, ()))


def _receiver_words(rule) -> set[bytes] | None:
    """Words naming the receiver of a receiver-specific rule, or None when any receiver will do."""
    kind = rule[0]
    if kind == "root":  # a module or object the call is made on: `re.compile`, `ejs.render`
        return {(w + ".").encode() for w in rule[1]}
    if kind in ("base", "exact"):
        return {w.split("::")[-1].encode() for w in rule[1]}
    if kind == "has":
        return {w.encode() for w in rule[1]} | {w.capitalize().encode() for w in rule[1]} | {w.upper().encode() for w in rule[1]}
    if kind == "or":
        out: set[bytes] = set()
        for part in rule[1]:
            w = _receiver_words(part)
            if w is None:
                return None
            out |= w
        return out
    return None


def prefilter_words(lang: str, cwe: int) -> tuple[bytes, ...]:
    """Words whose presence in a file means the class can occur there (the method names of its rules)."""
    words: set[bytes] = set()
    for r in RULES.get(lang, {}).get(cwe, ()):
        recv_words = _receiver_words(r[1])
        if recv_words is not None:  # `ERB.new`, `Pattern.compile`: the receiver is the distinctive word, not `new`
            words.update(recv_words)
            continue
        for n in r[0]:
            if n == CAP:  # `new Model(req.body)`: the file must reference the whole body
                words.update((b"req.body", b"request.body", b"ctx.request.body"))
            else:
                words.add(n.encode())
    if cwe == 942:
        return (b"Access-Control-Allow-Origin", b"access-control-allow-origin")
    words |= set(CUSTOM_WORDS.get(cwe, ()))
    return tuple(sorted(words))
