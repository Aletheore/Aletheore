"""Deterministic database-schema extraction from a repository's own DDL
migrations - Postgres, MySQL, SQLite, SQL Server, or Oracle, detected from
the repo's own config (see the dialect-detection note below).

Produces the `repository.database.schema` AIR section: the tables, columns,
foreign-key relations, indexes, and CHECK constraints a repository's
migrations define, each resolving to the file:line that introduced it.

Every relation carries a `name`: an explicit `CONSTRAINT name FOREIGN KEY
...` uses it as given, and an unnamed single-column FK is auto-named
following Postgres' own real, documented default convention
(`<table>_<column>_fkey`) rather than left nameless - not a guess, the
same name Postgres itself would assign. This is what makes a later `ALTER
TABLE ... DROP CONSTRAINT <name>` resolvable (removes the matching
relation) instead of always falling to `unsupported`; a name that matches
no tracked relation - most likely a named UNIQUE/CHECK/PRIMARY KEY
constraint, not individually addressable yet - still falls back to
`unsupported` with the real statement text, never silently no-ops.

Parsing is via sqlglot rather than a hand-written tokenizer (which this
module used through 2026-09-04). The hand-written tokenizer silently
dropped every table-level constraint except FOREIGN KEY - a composite
`PRIMARY KEY (a, b)` (the ordinary shape for every join/junction table)
came back with no column marked `primary_key` at all, with no
`unsupported` entry to say why. sqlglot is MIT-licensed, has zero
transitive dependencies of its own (a pure-Python wheel, not a compiled
grammar needing per-platform wheels), and its AST cleanly discriminates
every CREATE/ALTER/DROP action this module replays. Line numbers are not
exposed on sqlglot's parsed expressions, so this module splits the file
into statements itself first using sqlglot's own tokenizer (which already
handles Postgres string literals and dollar-quoted bodies correctly - no
need to re-track quote/comment/depth state by hand) purely to record each
statement's starting line, then parses each statement's text individually.

Why not regex, still: a backtracking pattern over attacker-supplied SQL is
the exact shape of the ReDoS fixed in PR #190. Not a concern here - this
module never builds its own backtracking pattern; sqlglot's tokenizer is a
linear-time hand-written scanner, the same design this module used before.

The SQL dialect (postgres/mysql/sqlite/tsql/oracle) is read from a real,
explicit config file the repo itself already has - a Prisma
`schema.prisma`'s `datasource` block, `alembic.ini`'s `sqlalchemy.url`
scheme, Rails' `database.yml` `adapter:`, Django `settings.py`'s `ENGINE`,
or a `knexfile.js`/`.sequelizerc`'s `client`/`dialect` - never inferred
from the SQL text itself (see `_detect_sql_dialect`): a wrong guess there
could silently produce a plausible-looking but factually wrong schema, a
materially worse failure mode than this module's ordinary honest
"recorded as unsupported" fallback. Falls back to postgres, this module's
original and still most common target, when no such file is found.

Deliberately narrower than any of those dialects, regardless of which one
is active: CREATE TABLE, CREATE INDEX, DROP TABLE, DROP INDEX, every
ALTER TABLE action with a clear, unambiguous DDL meaning (ADD/DROP/RENAME
COLUMN, RENAME TO, ALTER COLUMN TYPE/SET-DROP NOT NULL/SET DEFAULT, ADD
CONSTRAINT UNIQUE/FOREIGN KEY/PRIMARY KEY/CHECK, and - where the name
resolves - DROP CONSTRAINT), and CHECK constraints (column- or
table-level, named or not - stored as `{name, column, expression}` on the
owning table, `expression` the real reconstructed SQL text rather than an
evaluated boolean, since evaluating an arbitrary CHECK expression is not
this module's job) are modeled. EXCLUDE constraints, an unresolvable DROP
CONSTRAINT, views, sequences, types, extensions, functions/triggers,
GRANT/REVOKE, and raw DML are recorded in `unsupported` (with the real
reconstructed SQL text, not a truncated token dump) rather than modeled -
a repository is free to contain SQL this does not model, and a partial
schema is more useful than a failed scan. Never raises on malformed SQL: a
statement sqlglot cannot parse falls back to a generic Command node scoped
to that one statement, not the whole file; a file with a truly
unterminated string or dollar-quote (which sqlglot's tokenizer cannot
recover from at all, unlike an ordinary unparseable statement) is
re-tokenized up to the error's own reported position to salvage whatever
complete statements came before it, with an `unsupported` entry noting the
rest of the file was unreadable.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import TokenError

from aletheore.scanner.detect import IGNORED_DIRS

from aletheore.orm_migrations import (
    extract_alembic_migrations,
    extract_django_migrations,
    extract_rails_migrations,
)

# sqlglot logs a warning per statement it can't fully parse ("Falling back
# to parsing as a 'Command'") - this module already surfaces that same
# information as a real `unsupported` entry with the actual SQL text, so
# the log line would just be duplicate noise on every scan of a repo with
# any non-modeled SQL in it.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

_DEFAULT_SQL_DIALECT = "postgres"

# Set once per extract_schema() call (see _detect_sql_dialect) and read by
# every parse/render call below. A plain module variable rather than a
# parameter threaded through every function that touches SQL text - safe
# because this module has no concurrent or re-entrant call pattern
# (extract_schema always runs to completion, including its raw_sql
# recursion for RunSQL/execute/op.execute, before another call can start).
_SQL_DIALECT = _DEFAULT_SQL_DIALECT

# Real config files that explicitly declare a project's SQL dialect, and
# how to read each one - never inferred from the SQL text itself. A wrong
# guess there could silently produce a plausible-looking but factually
# wrong schema, a materially worse failure mode than this module's
# ordinary honest "recorded as unsupported" fallback. Falls back to
# _DEFAULT_SQL_DIALECT when no such file is found, which is this module's
# original and still most common target.
_PRISMA_PROVIDER_TO_DIALECT = {
    "postgresql": "postgres", "postgres": "postgres", "cockroachdb": "postgres",
    "mysql": "mysql", "sqlite": "sqlite", "sqlserver": "tsql",
}
_URL_SCHEME_TO_DIALECT = {
    "postgresql": "postgres", "postgres": "postgres", "cockroachdb": "postgres",
    "mysql": "mysql", "sqlite": "sqlite", "mssql": "tsql", "oracle": "oracle",
}
_RAILS_ADAPTER_TO_DIALECT = {
    "postgresql": "postgres", "mysql2": "mysql", "mysql": "mysql", "sqlite3": "sqlite",
    "sqlserver": "tsql",
}
_DJANGO_ENGINE_TO_DIALECT = {
    "postgresql": "postgres", "postgresql_psycopg2": "postgres", "mysql": "mysql",
    "sqlite3": "sqlite", "oracle": "oracle",
}
_JS_CLIENT_TO_DIALECT = {
    "pg": "postgres", "postgres": "postgres", "postgresql": "postgres",
    "mysql": "mysql", "mysql2": "mysql", "sqlite3": "sqlite", "mssql": "tsql",
}


def _dialect_from_config_file(path: Path, filename: str) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    if filename == "schema.prisma":
        match = re.search(r'datasource\s+\w+\s*\{[^}]*?provider\s*=\s*"(\w+)"', text, re.DOTALL)
        return _PRISMA_PROVIDER_TO_DIALECT.get(match.group(1)) if match else None
    if filename == "alembic.ini":
        match = re.search(r"sqlalchemy\.url\s*=\s*([a-zA-Z0-9+]+)://", text)
        if not match:
            return None
        scheme = match.group(1).split("+")[0]
        return _URL_SCHEME_TO_DIALECT.get(scheme)
    if filename == "database.yml":
        match = re.search(r"^\s*adapter:\s*(\w+)", text, re.MULTILINE)
        return _RAILS_ADAPTER_TO_DIALECT.get(match.group(1)) if match else None
    if filename == "settings.py":
        match = re.search(r"django\.db\.backends\.(\w+)", text)
        return _DJANGO_ENGINE_TO_DIALECT.get(match.group(1)) if match else None
    if filename in ("knexfile.js", ".sequelizerc"):
        match = re.search(r"""(?:client|dialect)\s*:\s*['"](\w+)['"]""", text)
        return _JS_CLIENT_TO_DIALECT.get(match.group(1)) if match else None
    return None


_DIALECT_CONFIG_FILENAMES = frozenset(
    {"schema.prisma", "alembic.ini", "database.yml", "settings.py", "knexfile.js", ".sequelizerc"}
)


def _detect_sql_dialect(repo_path: Path) -> str:
    """The real, explicit dialect a repo's own config declares. Walks the
    tree once (pruning the same noise directories the rest of the scanner
    ignores) looking for the first recognized config file with a resolvable
    provider/adapter/URL-scheme value; the first match wins. Returns
    _DEFAULT_SQL_DIALECT when nothing is found."""
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            if filename not in _DIALECT_CONFIG_FILENAMES:
                continue
            dialect = _dialect_from_config_file(Path(dirpath) / filename, filename)
            if dialect is not None:
                return dialect
    return _DEFAULT_SQL_DIALECT


# A migration file's own path can name its dialect directly - real,
# common convention for a project that ships migrations for more than one
# backend (Rust/Diesel apps in particular document this exact layout:
# migrations/postgresql/, migrations/mysql/, migrations/sqlite/, each with
# its own real dialect-specific SQL - confirmed directly on a real repo,
# where scanning all three together under one guessed dialect would have
# mis-parsed two thirds of the files). Checked per file, before falling
# back to the repo-wide config-file signal - it is closer to the actual
# SQL being read, so it wins when the two would ever disagree (e.g. a
# monorepo with one schema.prisma but a differently-shaped raw-SQL
# migration set elsewhere).
_DIALECT_DIR_NAMES = {
    "postgres": "postgres", "postgresql": "postgres", "pg": "postgres",
    "cockroachdb": "postgres",
    "mysql": "mysql", "mariadb": "mysql",
    "sqlite": "sqlite", "sqlite3": "sqlite",
    "mssql": "tsql", "sqlserver": "tsql", "tsql": "tsql",
    "oracle": "oracle",
}


def _dialect_from_path(rel_path: str) -> str | None:
    for part in Path(rel_path).parts[:-1]:
        dialect = _DIALECT_DIR_NAMES.get(part.lower())
        if dialect is not None:
            return dialect
    return None


_SUMMARY_LIMIT = 80


def _summarize(statement: str) -> str:
    """First line of a statement, length-capped.

    Bounded because this string travels into AIR, the dashboard, and LLM
    prompts - an unbounded slice of a migration file would let one
    pathological statement dominate all three.
    """
    collapsed = " ".join(statement.replace("\n", " ").split())
    if len(collapsed) <= _SUMMARY_LIMIT:
        return collapsed
    return collapsed[: _SUMMARY_LIMIT - 1] + "…"


_MAX_MIGRATION_BYTES = 2 * 1024 * 1024


def _iter_migration_files(repo_path: Path, migration_directories: list[str]) -> list[Path]:
    """Every .sql file under the given directories, in a stable order.

    Sorted by POSIX-relative path, not by os.walk order: migrations replay
    sequentially, so their order *is* the resulting schema. Leaving it to the
    filesystem would make the same repository produce different columns on
    APFS than on ext4 - the exact class of bug PR #192 fixed for detector
    arrays, and worse here because order changes content rather than just
    presentation.
    """
    files: list[Path] = []
    for directory in migration_directories:
        base = repo_path / directory
        if not base.is_dir():
            continue
        for candidate in base.rglob("*.sql"):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            files.append(candidate)
    return sorted(files, key=lambda path: path.relative_to(repo_path).as_posix())


_BODY_STATEMENT_KINDS = frozenset(
    {sqlglot.TokenType.TRIGGER, sqlglot.TokenType.FUNCTION, sqlglot.TokenType.PROCEDURE}
)


def _tokenize_statements(text: str, tokens: list) -> list[tuple[str, int, bool]]:
    """(statement_text, start_line, is_body_statement) for every
    semicolon-terminated statement among already-tokenized `tokens`, in
    source order.

    A Postgres function's dollar-quoted body is already consumed into a
    single token by the tokenizer, confirmed directly, so it never reaches
    this loop as separate statements to begin with. MySQL/SQLite
    triggers/functions/procedures use a bare `BEGIN ... END` body instead
    (no dollar-quoting), whose own internal semicolons are ordinary
    top-level SEMICOLON tokens - found via a real repo (usememos/memos'
    SQLite trigger migrations): a naive split broke each trigger into a
    truncated fragment plus a stray bare "END" fragment. BEGIN/END depth
    is tracked once a statement is confirmed (by its own first few tokens)
    to be a CREATE TRIGGER/FUNCTION/PROCEDURE - deliberately not tracked
    for any other statement, so an ordinary `BEGIN;`/`COMMIT;` transaction
    pair (ubiquitous, and already correctly split as trivial standalone
    statements) is untouched.

    `is_body_statement` matters one level up too: sqlglot.parse() does its
    *own* semicolon-based splitting internally, with no BEGIN/END
    awareness at all, so even handing it this correctly-bounded chunk
    still produces the same fragments back (a Command for the CREATE
    TRIGGER, a stray separate EndStatement for the bare "END") - confirmed
    directly. The caller uses this flag to skip sqlglot.parse() entirely
    for a body statement and record it as one clean unsupported entry
    instead, which is correct anyway since none of the three is modeled.
    """
    statements: list[tuple[str, int, bool]] = []
    start = 0
    start_line = 1
    open_statement = False
    is_create = False
    is_body_statement = False
    begin_depth = 0
    stmt_token_count = 0
    for token in tokens:
        if not open_statement:
            start = token.start
            start_line = token.line
            open_statement = True
            is_create = False
            is_body_statement = False
            begin_depth = 0
            stmt_token_count = 0
        stmt_token_count += 1
        if stmt_token_count == 1:
            is_create = token.token_type == sqlglot.TokenType.CREATE
        elif is_create and not is_body_statement and stmt_token_count <= 5:
            if token.token_type in _BODY_STATEMENT_KINDS:
                is_body_statement = True
        if is_body_statement:
            if token.token_type == sqlglot.TokenType.BEGIN:
                begin_depth += 1
            elif token.token_type == sqlglot.TokenType.END:
                begin_depth = max(0, begin_depth - 1)
        if token.token_type == sqlglot.TokenType.SEMICOLON and (not is_body_statement or begin_depth == 0):
            chunk = text[start : token.start].strip()
            if chunk:
                statements.append((chunk, start_line, is_body_statement))
            open_statement = False
    if open_statement:
        chunk = text[start:].strip()
        if chunk:
            statements.append((chunk, start_line, is_body_statement))
    return statements


def _split_sql_statements(text: str) -> tuple[list[tuple[str, int, bool]], bool]:
    """(statements, truncated) - statements is (statement_text, start_line,
    is_body_statement) for every semicolon-terminated statement in the
    file, in source order; truncated is True when a tokenizer-fatal error
    (an unterminated string
    or dollar-quote - the one failure mode sqlglot's tokenizer cannot
    isolate to a single statement the way it isolates an ordinary
    unparseable statement to a Command node) meant part of the file could
    not be read.

    Splits purely on top-level SEMICOLON tokens from sqlglot's own
    tokenizer - a semicolon inside a string literal or a dollar-quoted
    function body is already consumed into that single token by the
    tokenizer, confirmed directly, so no separate depth/quote tracking is
    needed here the way the old hand-written cursor required.

    Never raises: on a tokenizer-fatal error, retries against the text up
    to the error's own reported start offset - a known-good prefix - to
    salvage whatever complete statements came before the broken part
    rather than losing the whole file to one downstream typo.
    """
    try:
        return _tokenize_statements(text, sqlglot.tokenize(text, read=_SQL_DIALECT)), False
    except TokenError as error:
        prefix_end = error.start if isinstance(error.start, int) else 0
        if prefix_end <= 0:
            return [], True
        try:
            prefix_tokens = sqlglot.tokenize(text[:prefix_end], read=_SQL_DIALECT)
        except TokenError:
            return [], True
        return _tokenize_statements(text[:prefix_end], prefix_tokens), True


def _sql_type_text(coldef: exp.ColumnDef) -> str:
    kind = coldef.args.get("kind")
    return kind.sql(dialect=_SQL_DIALECT).upper() if kind is not None else "UNKNOWN"


def _sql_on_delete(options: list | None) -> str | None:
    for option in options or []:
        text = str(option).upper()
        if text.startswith("ON DELETE "):
            return text[len("ON DELETE ") :]
    return None


def _sql_relation_from_reference(
    local_column: str, reference: exp.Expression, rel_path: str, line: int,
    *, table: str | None = None, name: str | None = None,
) -> dict | None:
    target = reference.args.get("this")
    if target is None:
        return None
    table_node = target.args.get("this") if isinstance(target, exp.Schema) else target
    if table_node is None:
        return None
    to_table = table_node.name
    to_columns = target.expressions if isinstance(target, exp.Schema) else []
    to_column = to_columns[0].name if to_columns else "id"
    if not to_table:
        return None
    # An explicit `CONSTRAINT name ...` wins; otherwise `<table>_<column>_fkey`
    # is the real, documented Postgres default naming convention for an
    # unnamed single-column FK constraint - not a guess, but specific to
    # Postgres (MySQL's own auto-naming is a sequential `<table>_ibfk_<n>`,
    # not derivable from the column name at all; SQLite doesn't assign one
    # the same way). Applying the Postgres convention under a different
    # active dialect would fabricate a name that's simply wrong, so it's
    # gated on _SQL_DIALECT rather than applied unconditionally.
    constraint_name = name or (
        f"{table}_{local_column}_fkey" if table and _SQL_DIALECT == "postgres" else None
    )
    return {
        "name": constraint_name,
        "from_column": local_column,
        "to_table": to_table,
        "to_column": to_column,
        "on_delete": _sql_on_delete(reference.args.get("options")),
        "file": rel_path,
        "line": line,
    }


def _sql_column_from_columndef(
    coldef: exp.ColumnDef, rel_path: str, line: int, *, table: str | None = None
) -> tuple[dict, dict | None, list[dict], list[dict]]:
    column = {
        "name": coldef.name, "type": _sql_type_text(coldef), "primary_key": False,
        "nullable": True, "unique": False, "default": None, "file": rel_path, "line": line,
    }
    relation = None
    unsupported: list[dict] = []
    checks: list[dict] = []
    for constraint in coldef.args.get("constraints") or []:
        kind = constraint.kind
        if isinstance(kind, exp.PrimaryKeyColumnConstraint):
            column["primary_key"] = True
            # A PRIMARY KEY column is NOT NULL by definition in Postgres,
            # whether or not the migration spells it out - recording it as
            # nullable would make the diagram contradict the database.
            column["nullable"] = False
        elif isinstance(kind, exp.NotNullColumnConstraint):
            column["nullable"] = bool(kind.args.get("allow_null"))
        elif isinstance(kind, exp.UniqueColumnConstraint):
            column["unique"] = True
        elif isinstance(kind, exp.DefaultColumnConstraint):
            default_node = kind.args.get("this")
            if default_node is not None:
                column["default"] = default_node.sql(dialect=_SQL_DIALECT)
        elif isinstance(kind, exp.Reference):
            relation = _sql_relation_from_reference(column["name"], kind, rel_path, line, table=table)
        elif isinstance(kind, exp.CheckColumnConstraint):
            checks.append(
                {"name": None, "column": column["name"],
                 "expression": kind.args["this"].sql(dialect=_SQL_DIALECT),
                 "file": rel_path, "line": line}
            )
        else:
            # COLLATE, GENERATED AS IDENTITY, EXCLUDE-shaped column
            # constraints: real SQL, no shape-changing effect this module
            # models, recorded with the real reconstructed text rather than
            # silently dropped.
            unsupported.append(
                {"file": rel_path, "line": line,
                 "statement": _summarize(f"{column['name']} {kind.sql(dialect=_SQL_DIALECT)}")}
            )
    return column, relation, checks, unsupported


def _sql_foreign_key_relations(
    fk: exp.ForeignKey, rel_path: str, line: int, *, table: str | None = None, name: str | None = None
) -> list[dict]:
    local_columns = [c.name for c in fk.args.get("expressions") or []]
    reference = fk.args.get("reference")
    if not local_columns or reference is None:
        return []
    relations = []
    # A composite FK's auto-generated Postgres name isn't reliably
    # predictable from the column list alone, so auto-naming (as opposed to
    # an explicit CONSTRAINT name) is only attempted for the common
    # single-column case - `naming_table` is left unset otherwise so
    # _sql_relation_from_reference's own fallback doesn't guess one either.
    naming_table = table if (name or len(local_columns) == 1) else None
    for local_column in local_columns:
        relation = _sql_relation_from_reference(
            local_column, reference, rel_path, line, table=naming_table, name=name
        )
        if relation is not None:
            relations.append(relation)
    return relations


def _sql_apply_table_constraint(
    node: exp.Expression,
    by_name: dict[str, dict],
    relations: list[dict],
    checks: list[dict],
    unsupported: list[dict],
    rel_path: str,
    line: int,
    label: str,
    *,
    table: str | None = None,
    constraint_name: str | None = None,
) -> None:
    """Applies one table-level constraint (PRIMARY KEY/UNIQUE/FOREIGN KEY/
    CHECK, bare or wrapped in a named `CONSTRAINT name (...)`) to the
    columns already built for this table. `label` is what an unhandled
    constraint is reported against (the table name for a bare constraint,
    the constraint's own name for a named one)."""
    if isinstance(node, exp.PrimaryKey):
        for identifier in node.expressions:
            name = identifier.name if hasattr(identifier, "name") else str(identifier)
            column = by_name.get(name)
            if column is not None:
                column["primary_key"] = True
                column["nullable"] = False
    elif isinstance(node, exp.UniqueColumnConstraint):
        target = node.args.get("this")
        names = target.expressions if isinstance(target, exp.Schema) else []
        for identifier in names:
            column = by_name.get(identifier.name)
            if column is not None:
                column["unique"] = True
    elif isinstance(node, exp.ForeignKey):
        relations.extend(
            _sql_foreign_key_relations(node, rel_path, line, table=table, name=constraint_name)
        )
    elif isinstance(node, exp.CheckColumnConstraint):
        checks.append(
            {"name": constraint_name, "column": None,
             "expression": node.args["this"].sql(dialect=_SQL_DIALECT), "file": rel_path, "line": line}
        )
    else:
        unsupported.append(
            {"file": rel_path, "line": line,
             "statement": _summarize(f"{label} {node.sql(dialect=_SQL_DIALECT)}")}
        )


def _sql_create_table_event(stmt: exp.Create, rel_path: str, line: int) -> tuple[dict | None, list[dict]]:
    """A `CREATE TABLE` statement -> (create_table event, unsupported list).

    Table-level constraints (composite PRIMARY KEY/UNIQUE, CHECK, EXCLUDE)
    are handled in a second pass over the already-built column list, since
    they refer to columns by name rather than carrying their own.
    """
    schema = stmt.this
    table_node = schema.args.get("this") if isinstance(schema, exp.Schema) else schema
    if table_node is None or not table_node.name:
        return None, []
    table = table_node.name

    columns: list[dict] = []
    relations: list[dict] = []
    checks: list[dict] = []
    unsupported: list[dict] = []
    by_name: dict[str, dict] = {}

    expressions = schema.expressions if isinstance(schema, exp.Schema) else []
    for member in expressions:
        if isinstance(member, exp.ColumnDef):
            column, relation, column_checks, column_unsupported = _sql_column_from_columndef(
                member, rel_path, line, table=table
            )
            columns.append(column)
            by_name[column["name"]] = column
            if relation is not None:
                relations.append(relation)
            checks.extend(column_checks)
            unsupported.extend(column_unsupported)
        elif isinstance(member, exp.Constraint):
            for inner in member.expressions:
                _sql_apply_table_constraint(
                    inner, by_name, relations, checks, unsupported, rel_path, line,
                    f"CONSTRAINT {member.name}", table=table, constraint_name=member.name,
                )
        elif isinstance(member, (exp.PrimaryKey, exp.UniqueColumnConstraint, exp.ForeignKey, exp.CheckColumnConstraint)):
            _sql_apply_table_constraint(
                member, by_name, relations, checks, unsupported, rel_path, line, table, table=table
            )
        else:
            unsupported.append(
                {"file": rel_path, "line": line,
                 "statement": _summarize(f"{table}: {member.sql(dialect=_SQL_DIALECT)}")}
            )

    return (
        {"kind": "create_table", "table": table, "file": rel_path, "line": line,
         "columns": columns, "relations": relations, "checks": checks},
        unsupported,
    )


def _sql_create_index_event(stmt: exp.Create, rel_path: str, line: int) -> dict | None:
    index = stmt.this
    if index is None:
        # Malformed/incomplete CREATE INDEX (e.g. a raw-SQL statement built
        # dynamically at runtime in the source migration, captured only as a
        # literal fragment) - sqlglot parses the keywords but leaves nothing
        # to fill the Index node.
        return None
    table_node = index.args.get("table")
    if table_node is None or not table_node.name:
        return None
    columns = []
    params = index.args.get("params")
    for ordered in (params.args.get("columns") if params is not None else None) or []:
        target = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        if hasattr(target, "name") and target.name:
            columns.append(target.name)
    return {
        "kind": "create_index", "table": table_node.name, "name": index.name,
        "columns": columns, "unique": bool(stmt.args.get("unique")),
        "file": rel_path, "line": line,
    }


def _sql_alter_table_events(stmt: exp.Alter, rel_path: str, line: int) -> list[dict]:
    table_node = stmt.this
    if table_node is None or not table_node.name:
        return []
    table = table_node.name
    events: list[dict] = []

    for action in stmt.args.get("actions") or []:
        if isinstance(action, exp.ColumnDef):
            column, relation, column_checks, column_unsupported = _sql_column_from_columndef(
                action, rel_path, line, table=table
            )
            events.extend(
                {"kind": "add_check", "table": table, "check": check} for check in column_checks
            )
            events.extend(
                {"kind": "unsupported", **entry} for entry in column_unsupported
            )
            events.append(
                {"kind": "add_column", "table": table, "file": rel_path, "line": line,
                 "column": column, "relation": relation}
            )
        elif isinstance(action, exp.Drop) and action.args.get("kind") == "COLUMN":
            for target in action.args.get("tables") or []:
                if target.name:
                    events.append(
                        {"kind": "remove_column", "table": table, "name": target.name,
                         "file": rel_path, "line": line}
                    )
        elif isinstance(action, exp.Drop) and action.args.get("kind") == "CONSTRAINT":
            for target in action.args.get("tables") or []:
                if not target.name:
                    continue
                # Most real DROP CONSTRAINT targets are foreign keys, whose
                # constraint name (explicit or Postgres' own auto-generated
                # <table>_<column>_fkey) is now tracked on the relation - so
                # this can actually be resolved instead of only ever being
                # unsupported. Falls back to unsupported (with the real
                # statement text) at merge time if no tracked relation has
                # that name - a named UNIQUE/CHECK/PK constraint, most
                # likely, which isn't individually addressable yet.
                events.append(
                    {"kind": "remove_relation", "name": target.name, "file": rel_path, "line": line,
                     "fallback_statement": _summarize(f"ALTER TABLE {table} DROP CONSTRAINT {target.name}")}
                )
        elif isinstance(action, exp.RenameColumn):
            old_name = action.args.get("this")
            new_name = action.args.get("to")
            if old_name is not None and new_name is not None:
                events.append(
                    {"kind": "rename_column", "table": table, "old_name": old_name.name,
                     "new_name": new_name.name, "file": rel_path, "line": line}
                )
        elif isinstance(action, exp.AlterRename):
            # `RENAME old TO new` (COLUMN keyword optional in Postgres) and
            # `RENAME TO new` (whole-table rename) parse to the same
            # AlterRename action - confirmed directly, the only
            # distinguishing signal is a stray top-level ToTableProperty in
            # the statement's own `options` for the column-rename shorthand,
            # holding the real new name (AlterRename.this ends up holding
            # the *old* column name in that case, not a new table name).
            # Without this check, `RENAME description TO readme` silently
            # renamed the whole table to "description" - found via a real
            # migration (coder/coder), losing the table under its expected
            # name for every subsequent migration that referenced it.
            to_table_property = next(
                (o for o in (stmt.args.get("options") or []) if isinstance(o, exp.ToTableProperty)),
                None,
            )
            if to_table_property is not None:
                old_name = action.args.get("this")
                new_name = to_table_property.args.get("this")
                if old_name is not None and old_name.name and new_name is not None and new_name.name:
                    events.append(
                        {"kind": "rename_column", "table": table, "old_name": old_name.name,
                         "new_name": new_name.name, "file": rel_path, "line": line}
                    )
                continue
            new_table = action.args.get("this")
            if new_table is not None and new_table.name:
                events.append(
                    {"kind": "rename_table", "old_table": table, "new_table": new_table.name}
                )
        elif isinstance(action, exp.AlterColumn):
            col_node = action.args.get("this")
            if col_node is None or not col_node.name:
                continue
            changes: dict = {}
            dtype = action.args.get("dtype")
            if dtype is not None:
                changes["type"] = dtype.sql(dialect=_SQL_DIALECT).upper()
            if "allow_null" in action.args:
                changes["nullable"] = bool(action.args.get("allow_null"))
            if "default" in action.args and action.args.get("default") is not None:
                changes["default"] = action.args["default"].sql(dialect=_SQL_DIALECT)
            if changes:
                events.append(
                    {"kind": "alter_column", "table": table, "name": col_node.name,
                     "changes": changes, "file": rel_path, "line": line}
                )
        elif isinstance(action, exp.ModifyColumn):
            # MySQL's CHANGE COLUMN (old_name new_name type ...) and MODIFY
            # COLUMN (col type ...) both parse to this one action - the
            # only difference is whether rename_from is present. The new
            # ColumnDef carries the column's full post-change definition,
            # so it's rebuilt the same way a real ADD COLUMN's ColumnDef
            # is, then applied as a rename (if renamed) plus a type/
            # constraint update - never invented, all read from the
            # statement's own new definition.
            coldef = action.args.get("this")
            if coldef is None or not coldef.name:
                continue
            new_column, relation, column_checks, column_unsupported = _sql_column_from_columndef(
                coldef, rel_path, line, table=table
            )
            rename_from = action.args.get("rename_from")
            events.extend({"kind": "add_check", "table": table, "check": check} for check in column_checks)
            events.extend({"kind": "unsupported", **entry} for entry in column_unsupported)
            if rename_from is not None and rename_from.name != new_column["name"]:
                events.append(
                    {"kind": "rename_column", "table": table, "old_name": rename_from.name,
                     "new_name": new_column["name"], "file": rel_path, "line": line}
                )
            # type/nullable are what CHANGE/MODIFY COLUMN is always really
            # about, so always applied. unique/default/primary_key are only
            # applied when the new definition actually states them - unlike
            # type/nullable, a real CHANGE/MODIFY COLUMN that stays silent
            # on these isn't necessarily dropping them, and this module's
            # own column-building always initializes them False/None when
            # a statement doesn't mention them, indistinguishable here from
            # an explicit clear - so, to avoid clobbering a UNIQUE/DEFAULT/
            # PRIMARY KEY a separate earlier constraint already set,
            # forward only the ones this statement positively asserts.
            changes = {"type": new_column["type"], "nullable": new_column["nullable"]}
            if new_column["unique"]:
                changes["unique"] = True
            if new_column["default"] is not None:
                changes["default"] = new_column["default"]
            if new_column["primary_key"]:
                changes["primary_key"] = True
            events.append(
                {"kind": "alter_column", "table": table, "name": new_column["name"],
                 "changes": changes, "file": rel_path, "line": line}
            )
            if relation is not None:
                events.append(
                    {"kind": "add_relation", "table": table, "relation": relation,
                     "file": rel_path, "line": line}
                )
        elif isinstance(action, exp.DropPrimaryKey):
            events.append({"kind": "drop_primary_key", "table": table})
        elif isinstance(action, exp.AddConstraint):
            for wrapper in action.expressions:
                explicit_name = wrapper.name if isinstance(wrapper, exp.Constraint) else None
                inner_list = wrapper.expressions if isinstance(wrapper, exp.Constraint) else [wrapper]
                for inner in inner_list:
                    if isinstance(inner, exp.ForeignKey):
                        for relation in _sql_foreign_key_relations(
                            inner, rel_path, line, table=table, name=explicit_name
                        ):
                            events.append(
                                {"kind": "add_relation", "table": table, "relation": relation,
                                 "file": rel_path, "line": line}
                            )
                    elif isinstance(inner, exp.UniqueColumnConstraint):
                        target = inner.args.get("this")
                        names = target.expressions if isinstance(target, exp.Schema) else []
                        for identifier in names:
                            events.append(
                                {"kind": "alter_column", "table": table, "name": identifier.name,
                                 "changes": {"unique": True}, "file": rel_path, "line": line}
                            )
                    elif isinstance(inner, (exp.PrimaryKeyColumnConstraint, exp.PrimaryKey)):
                        names = inner.expressions if isinstance(inner, exp.PrimaryKey) else []
                        for identifier in names:
                            events.append(
                                {"kind": "alter_column", "table": table, "name": identifier.name,
                                 "changes": {"primary_key": True, "nullable": False},
                                 "file": rel_path, "line": line}
                            )
                    elif isinstance(inner, exp.CheckColumnConstraint):
                        events.append(
                            {"kind": "add_check", "table": table,
                             "check": {"name": explicit_name, "column": None,
                                       "expression": inner.args["this"].sql(dialect=_SQL_DIALECT),
                                       "file": rel_path, "line": line}}
                        )
                    else:
                        events.append(
                            {"kind": "unsupported", "file": rel_path, "line": line,
                             "statement": _summarize(f"ALTER TABLE {table} ADD CONSTRAINT {inner.sql(dialect=_SQL_DIALECT)}")}
                        )
        else:
            events.append(
                {"kind": "unsupported", "file": rel_path, "line": line,
                 "statement": _summarize(f"ALTER TABLE {table} {action.sql(dialect=_SQL_DIALECT)}")}
            )

    return events


def _sql_events_from_statement(stmt: exp.Expression, rel_path: str, line: int) -> tuple[list[dict], list[dict]]:
    """One parsed statement -> (events, unsupported)."""
    if isinstance(stmt, exp.Create) and stmt.args.get("kind") == "TABLE":
        event, unsupported = _sql_create_table_event(stmt, rel_path, line)
        return ([event] if event is not None else []), unsupported
    if isinstance(stmt, exp.Create) and stmt.args.get("kind") == "INDEX":
        event = _sql_create_index_event(stmt, rel_path, line)
        if event is not None:
            return [event], []
        text = _summarize(stmt.sql(dialect=_SQL_DIALECT))
        return [], [{"file": rel_path, "line": line, "statement": text}]
    if isinstance(stmt, exp.Alter) and stmt.args.get("kind") == "TABLE":
        return _sql_alter_table_events(stmt, rel_path, line), []
    if isinstance(stmt, exp.Alter) and stmt.args.get("kind") == "INDEX":
        index_node = stmt.this
        actions = stmt.args.get("actions") or []
        if index_node is not None and index_node.name and len(actions) == 1 and isinstance(actions[0], exp.AlterRename):
            new_name = actions[0].args.get("this")
            if new_name is not None and new_name.name:
                return [{"kind": "rename_index", "old_name": index_node.name, "new_name": new_name.name}], []
        text = _summarize(stmt.sql(dialect=_SQL_DIALECT))
        return [], [{"file": rel_path, "line": line, "statement": text}]
    if isinstance(stmt, exp.Drop) and stmt.args.get("kind") == "TABLE":
        events = [
            {"kind": "remove_table", "table": target.name}
            for target in stmt.args.get("tables") or []
            if target.name
        ]
        return events, []
    if isinstance(stmt, exp.Drop) and stmt.args.get("kind") == "INDEX":
        # No table context on a bare DROP INDEX - matched by name alone.
        events = [
            {"kind": "remove_index", "table": None, "name": target.name}
            for target in stmt.args.get("tables") or []
            if target.name
        ]
        return events, []

    text = _summarize(stmt.sql(dialect=_SQL_DIALECT))
    return [], [{"file": rel_path, "line": line, "statement": text}]


def _sql_events_from_text(text: str, rel_path: str) -> tuple[list[dict], list[dict]]:
    """Every migration-relevant event in one migration file's raw SQL text
    -> (events, unsupported)."""
    statements, truncated = _split_sql_statements(text)

    events: list[dict] = []
    unsupported: list[dict] = []
    if truncated:
        unsupported.append(
            {"file": rel_path, "line": 1,
             "statement": "rest of file could not be tokenized (unterminated string or dollar-quote)"}
        )
    for statement_text, line, is_body_statement in statements:
        if is_body_statement:
            # sqlglot.parse() does its own semicolon-based splitting with
            # no BEGIN/END awareness, so handing it this already correctly
            # bounded chunk would still re-fragment it (confirmed
            # directly: a Command for the CREATE TRIGGER/FUNCTION/
            # PROCEDURE plus a stray separate "END"). None of the three is
            # modeled anyway, so record it as one clean entry instead.
            unsupported.append(
                {"file": rel_path, "line": line, "statement": _summarize(statement_text)}
            )
            continue
        parsed = sqlglot.parse(statement_text, read=_SQL_DIALECT, error_level=sqlglot.ErrorLevel.IGNORE)
        for stmt in parsed:
            if stmt is None:
                continue
            stmt_events, stmt_unsupported = _sql_events_from_statement(stmt, rel_path, line)
            events.extend(stmt_events)
            unsupported.extend(stmt_unsupported)
    return events, unsupported


def _merge_schema_events(
    tables: dict[str, dict],
    relations: list[dict],
    indexes: list[dict],
    unsupported: list[dict],
    events: list[dict],
) -> None:
    """Folds a stream of pre-built schema events - from a real .sql file,
    an ORM migration (Django/Rails/Alembic - see orm_migrations.py), or a
    raw-SQL escape hatch inside one of those (Django's RunSQL, Rails'
    execute, Alembic's op.execute) - into the running schema, with
    consistent semantics regardless of source: a CREATE on a table that
    already exists is a no-op, an ADD COLUMN only applies to a table
    already known, columns keep declaration order."""
    for event in events:
        kind = event["kind"]
        if kind == "create_table":
            if event["table"] in tables:
                continue
            tables[event["table"]] = {
                "name": event["table"], "columns": event["columns"],
                "checks": list(event.get("checks", [])),
                "file": event["file"], "line": event["line"],
            }
            for relation in event["relations"]:
                relations.append({"from_table": event["table"], **relation})
        elif kind == "add_column":
            table = tables.get(event["table"])
            column = event.get("column")
            if table is None:
                # A migration set is partial (a squashed baseline, or a
                # table created outside these directories). Recorded
                # rather than inventing a table with one column, which
                # would render as a phantom node in the diagram.
                unsupported.append(
                    {
                        "file": event["file"],
                        "line": event["line"],
                        "statement": _summarize(
                            f"ADD COLUMN on an unknown table {event['table']}"
                        ),
                    }
                )
                continue
            if column is not None and not any(
                c["name"] == column["name"] for c in table["columns"]
            ):
                table["columns"].append(column)
            relation = event.get("relation")
            if relation is not None:
                relations.append({"from_table": event["table"], **relation})
        elif kind == "add_relation":
            relations.append({"from_table": event["table"], **event["relation"]})
        elif kind == "create_index":
            indexes.append(
                {"name": event["name"], "table": event["table"], "columns": event["columns"],
                 "unique": event["unique"], "file": event["file"], "line": event["line"]}
            )
        elif kind == "remove_column":
            table = tables.get(event["table"])
            if table is not None:
                table["columns"] = [c for c in table["columns"] if c["name"] != event["name"]]
        elif kind == "alter_column":
            table = tables.get(event["table"])
            column = None if table is None else next(
                (c for c in table["columns"] if c["name"] == event["name"]), None
            )
            if column is not None:
                for field in ("type", "nullable", "unique", "default", "primary_key"):
                    if field in event["changes"]:
                        column[field] = event["changes"][field]
        elif kind == "drop_primary_key":
            table = tables.get(event["table"])
            if table is not None:
                for column in table["columns"]:
                    if column["primary_key"]:
                        column["primary_key"] = False
        elif kind == "rename_column":
            table = tables.get(event["table"])
            column = None if table is None else next(
                (c for c in table["columns"] if c["name"] == event["old_name"]), None
            )
            if column is not None:
                column["name"] = event["new_name"]
        elif kind == "remove_table":
            tables.pop(event["table"], None)
            relations[:] = [
                r for r in relations
                if r["from_table"] != event["table"] and r["to_table"] != event["table"]
            ]
            indexes[:] = [i for i in indexes if i["table"] != event["table"]]
        elif kind == "rename_table":
            table = tables.pop(event["old_table"], None)
            if table is not None:
                table["name"] = event["new_table"]
                tables[event["new_table"]] = table
                for relation in relations:
                    if relation["from_table"] == event["old_table"]:
                        relation["from_table"] = event["new_table"]
                    if relation["to_table"] == event["old_table"]:
                        relation["to_table"] = event["new_table"]
                for index in indexes:
                    if index["table"] == event["old_table"]:
                        index["table"] = event["new_table"]
        elif kind == "remove_index":
            table = event.get("table")
            indexes[:] = [
                i for i in indexes
                if i["name"] != event["name"] or (table is not None and i["table"] != table)
            ]
        elif kind == "rename_index":
            for index in indexes:
                if index["name"] == event["old_name"]:
                    index["name"] = event["new_name"]
        elif kind == "add_check":
            table = tables.get(event["table"])
            if table is not None:
                table.setdefault("checks", []).append(event["check"])
        elif kind == "remove_relation":
            matched = [r for r in relations if r.get("name") == event["name"]]
            if matched:
                relations[:] = [r for r in relations if r.get("name") != event["name"]]
            else:
                # Not a tracked foreign key - most likely a named UNIQUE,
                # CHECK, or PRIMARY KEY constraint, which isn't individually
                # addressable by name yet. Recorded rather than silently
                # doing nothing, same as before this event kind existed.
                unsupported.append(
                    {"file": event["file"], "line": event["line"],
                     "statement": event["fallback_statement"]}
                )
        elif kind == "raw_sql":
            sql_events, sql_unsupported = _sql_events_from_text(event["sql"], event["file"])
            unsupported.extend(sql_unsupported)
            _merge_schema_events(tables, relations, indexes, unsupported, sql_events)
        elif kind == "unsupported":
            unsupported.append(
                {"file": event["file"], "line": event["line"], "statement": event["statement"]}
            )


def extract_schema(repo_path: Path, migration_directories: list[str]) -> dict:
    """Replay migration DDL into the schema it defines.

    Returns tables/relations/indexes sorted by name, plus the sources it read
    and the statements it did not model. Never raises on malformed SQL: a
    statement that cannot be parsed is recorded and skipped.
    """
    global _SQL_DIALECT
    repo_default_dialect = _detect_sql_dialect(repo_path)

    tables: dict[str, dict] = {}
    relations: list[dict] = []
    indexes: list[dict] = []
    unsupported: list[dict] = []
    sources: list[str] = []
    dialects_used: set[str] = set()

    # Parse every source's events first, grouped per file, without applying
    # any of them yet - replaying is deferred to a single pass below, sorted
    # by path across ALL sources together. A repo that adopted an ORM
    # partway through its history (or vice versa) mixes .sql migrations with
    # Django/Rails/Alembic ones; applying every .sql file before any ORM
    # migration - regardless of which one actually came first by path -
    # would run a later SQL statement against a table an earlier ORM
    # migration hadn't created yet.
    sql_events_by_file: dict[str, list[dict]] = {}
    for path in _iter_migration_files(repo_path, migration_directories):
        rel_path = path.relative_to(repo_path).as_posix()
        try:
            if path.stat().st_size > _MAX_MIGRATION_BYTES:
                unsupported.append(
                    {"file": rel_path, "line": 1, "statement": "file exceeds 2 MiB, not parsed"}
                )
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # A file's own path (e.g. migrations/mysql/...) wins over the
        # repo-wide config signal when both exist - see _dialect_from_path.
        _SQL_DIALECT = _dialect_from_path(rel_path) or repo_default_dialect
        dialects_used.add(_SQL_DIALECT)
        sources.append(rel_path)
        events, file_unsupported = _sql_events_from_text(text, rel_path)
        unsupported.extend(file_unsupported)
        sql_events_by_file[rel_path] = events

    # "postgresql" for display consistency with this module's original,
    # still most common target; every other detected dialect uses its own
    # sqlglot name (mysql/sqlite/tsql/oracle) since there's no established
    # display convention for those yet. dialects_used, not the now
    # per-file _SQL_DIALECT, so a repo whose migrations span more than one
    # real dialect (see _dialect_from_path) reports all of them, not just
    # whichever file happened to be read last.
    dialects: list[str] = sorted(
        "postgresql" if d == "postgres" else d for d in dialects_used
    )

    orm_events_by_file: dict[str, list[dict]] = {}
    for extractor, dialect_name in (
        (extract_django_migrations, "django"),
        (extract_rails_migrations, "rails"),
        (extract_alembic_migrations, "alembic"),
    ):
        orm_events, orm_sources = extractor(repo_path, migration_directories)
        if orm_sources:
            dialects.append(dialect_name)
            sources.extend(orm_sources)
            for event in orm_events:
                orm_events_by_file.setdefault(event["file"], []).append(event)

    # A given path is exactly one of SQL or ORM (the extension alone
    # decides), so grouping by file and replaying in path order across both
    # groups together is unambiguous - matching how each group already
    # ordered itself internally. _merge_schema_events already understands
    # both event shapes uniformly, so every file's events - regardless of
    # source - replay through the same call.
    events_by_file = sql_events_by_file | orm_events_by_file
    for rel_path in sorted(events_by_file):
        _merge_schema_events(tables, relations, indexes, unsupported, events_by_file[rel_path])

    # Sorted on every axis so identical migrations always produce identical
    # evidence. Columns keep their declaration order - that is the schema's
    # own order and carries meaning a sort would destroy.
    ordered_tables = [tables[name] for name in sorted(tables)]
    relations.sort(key=lambda r: (r["from_table"], r["from_column"], r["to_table"], r["to_column"]))
    indexes.sort(key=lambda i: (i["table"], i["name"]))
    unsupported.sort(key=lambda u: (u["file"], u["line"], u["statement"]))

    return {
        "checked": True,
        "reason": None,
        "dialect": sorted(set(dialects)) or None,
        "tables": ordered_tables,
        "relations": relations,
        "indexes": indexes,
        "unsupported": unsupported,
        "sources": sorted(sources),
    }


def skipped_schema(reason: str) -> dict:
    """The same shape with nothing in it.

    Mirrors dependency_vulnerabilities and api_endpoints: the section is
    always present with identical keys, so no consumer ever branches on
    whether it exists - only on `checked`.
    """
    return {
        "checked": False,
        "reason": reason,
        "dialect": None,
        "tables": [],
        "relations": [],
        "indexes": [],
        "unsupported": [],
        "sources": [],
    }
