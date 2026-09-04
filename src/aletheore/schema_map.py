"""Deterministic database-schema extraction from Postgres DDL migrations.

Produces the `repository.database.schema` AIR section: the tables, columns,
foreign-key relations, and indexes a repository's migrations define, each
resolving to the file:line that introduced it.

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

Scope is deliberately Postgres-only for v1 (`read="postgres"` is fixed,
though sqlglot itself supports 30+ dialects), and deliberately narrower
than Postgres: only CREATE TABLE, CREATE INDEX, DROP TABLE, DROP INDEX,
and every ALTER TABLE action with a clear, unambiguous DDL meaning (ADD/
DROP/RENAME COLUMN, RENAME TO, ALTER COLUMN TYPE/SET-DROP NOT NULL/SET
DEFAULT, ADD CONSTRAINT UNIQUE/FOREIGN KEY/PRIMARY KEY) are modeled.
CHECK/EXCLUDE constraints, DROP CONSTRAINT (which constraint gets dropped
isn't tracked precisely enough on relations to resolve), views, sequences,
types, extensions, functions/triggers, GRANT/REVOKE, and raw DML are
recorded in `unsupported` (with the real reconstructed SQL text, not a
truncated token dump) rather than modeled - a repository is free to
contain SQL this does not model, and a partial schema is more useful than
a failed scan. Never raises on malformed SQL: a statement sqlglot cannot
parse falls back to a generic Command node scoped to that one statement,
not the whole file; a file with a truly unterminated string or
dollar-quote (which sqlglot's tokenizer cannot recover from at all, unlike
an ordinary unparseable statement) is re-tokenized up to the error's own
reported position to salvage whatever complete statements came before it,
with an `unsupported` entry noting the rest of the file was unreadable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import TokenError

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

_SQL_DIALECT = "postgres"


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


def _tokenize_statements(text: str, tokens: list) -> list[tuple[str, int]]:
    """(statement_text, start_line) for every semicolon-terminated
    statement among already-tokenized `tokens`, in source order."""
    statements: list[tuple[str, int]] = []
    start = 0
    start_line = 1
    open_statement = False
    for token in tokens:
        if not open_statement:
            start = token.start
            start_line = token.line
            open_statement = True
        if token.token_type == sqlglot.TokenType.SEMICOLON:
            chunk = text[start : token.start].strip()
            if chunk:
                statements.append((chunk, start_line))
            open_statement = False
    if open_statement:
        chunk = text[start:].strip()
        if chunk:
            statements.append((chunk, start_line))
    return statements


def _split_sql_statements(text: str) -> tuple[list[tuple[str, int]], bool]:
    """(statements, truncated) - statements is (statement_text, start_line)
    for every semicolon-terminated statement in the file, in source order;
    truncated is True when a tokenizer-fatal error (an unterminated string
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
    local_column: str, reference: exp.Expression, rel_path: str, line: int
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
    return {
        "from_column": local_column,
        "to_table": to_table,
        "to_column": to_column,
        "on_delete": _sql_on_delete(reference.args.get("options")),
        "file": rel_path,
        "line": line,
    }


def _sql_column_from_columndef(
    coldef: exp.ColumnDef, rel_path: str, line: int
) -> tuple[dict, dict | None, list[dict]]:
    column = {
        "name": coldef.name, "type": _sql_type_text(coldef), "primary_key": False,
        "nullable": True, "unique": False, "default": None, "file": rel_path, "line": line,
    }
    relation = None
    unsupported: list[dict] = []
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
            relation = _sql_relation_from_reference(column["name"], kind, rel_path, line)
        else:
            # CHECK, COLLATE, GENERATED AS IDENTITY, EXCLUDE-shaped column
            # constraints: real SQL, no shape-changing effect this module
            # models, recorded with the real reconstructed text rather than
            # silently dropped.
            unsupported.append(
                {"file": rel_path, "line": line,
                 "statement": _summarize(f"{column['name']} {kind.sql(dialect=_SQL_DIALECT)}")}
            )
    return column, relation, unsupported


def _sql_foreign_key_relations(
    fk: exp.ForeignKey, rel_path: str, line: int
) -> list[dict]:
    local_columns = [c.name for c in fk.args.get("expressions") or []]
    reference = fk.args.get("reference")
    if not local_columns or reference is None:
        return []
    relations = []
    for local_column in local_columns:
        relation = _sql_relation_from_reference(local_column, reference, rel_path, line)
        if relation is not None:
            relations.append(relation)
    return relations


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
    unsupported: list[dict] = []
    by_name: dict[str, dict] = {}

    expressions = schema.expressions if isinstance(schema, exp.Schema) else []
    for member in expressions:
        if isinstance(member, exp.ColumnDef):
            column, relation, column_unsupported = _sql_column_from_columndef(member, rel_path, line)
            columns.append(column)
            by_name[column["name"]] = column
            if relation is not None:
                relations.append(relation)
            unsupported.extend(column_unsupported)
        elif isinstance(member, exp.PrimaryKey):
            for identifier in member.expressions:
                name = identifier.name if hasattr(identifier, "name") else str(identifier)
                column = by_name.get(name)
                if column is not None:
                    column["primary_key"] = True
                    column["nullable"] = False
        elif isinstance(member, exp.UniqueColumnConstraint):
            target = member.args.get("this")
            names = target.expressions if isinstance(target, exp.Schema) else []
            for identifier in names:
                column = by_name.get(identifier.name)
                if column is not None:
                    column["unique"] = True
        elif isinstance(member, exp.ForeignKey):
            relations.extend(_sql_foreign_key_relations(member, rel_path, line))
        elif isinstance(member, exp.Constraint):
            for inner in member.expressions:
                if isinstance(inner, exp.ForeignKey):
                    relations.extend(_sql_foreign_key_relations(inner, rel_path, line))
                elif isinstance(inner, exp.UniqueColumnConstraint):
                    target = inner.args.get("this")
                    names = target.expressions if isinstance(target, exp.Schema) else []
                    for identifier in names:
                        column = by_name.get(identifier.name)
                        if column is not None:
                            column["unique"] = True
                else:
                    unsupported.append(
                        {"file": rel_path, "line": line,
                         "statement": _summarize(f"CONSTRAINT {member.name} {inner.sql(dialect=_SQL_DIALECT)}")}
                    )
        else:
            unsupported.append(
                {"file": rel_path, "line": line,
                 "statement": _summarize(f"{table}: {member.sql(dialect=_SQL_DIALECT)}")}
            )

    return (
        {"kind": "create_table", "table": table, "file": rel_path, "line": line,
         "columns": columns, "relations": relations},
        unsupported,
    )


def _sql_create_index_event(stmt: exp.Create, rel_path: str, line: int) -> dict | None:
    index = stmt.this
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
            column, relation, column_unsupported = _sql_column_from_columndef(action, rel_path, line)
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
                events.append(
                    {"kind": "unsupported", "file": rel_path, "line": line,
                     "statement": _summarize(f"ALTER TABLE {table} DROP CONSTRAINT {target.name}")}
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
        elif isinstance(action, exp.AddConstraint):
            for wrapper in action.expressions:
                inner_list = wrapper.expressions if isinstance(wrapper, exp.Constraint) else [wrapper]
                for inner in inner_list:
                    if isinstance(inner, exp.ForeignKey):
                        for relation in _sql_foreign_key_relations(inner, rel_path, line):
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
        return ([event] if event is not None else []), []
    if isinstance(stmt, exp.Alter) and stmt.args.get("kind") == "TABLE":
        return _sql_alter_table_events(stmt, rel_path, line), []
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
    for statement_text, line in statements:
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
    tables: dict[str, dict] = {}
    relations: list[dict] = []
    indexes: list[dict] = []
    unsupported: list[dict] = []
    sources: list[str] = []

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

        sources.append(rel_path)
        events, file_unsupported = _sql_events_from_text(text, rel_path)
        unsupported.extend(file_unsupported)
        _merge_schema_events(tables, relations, indexes, unsupported, events)

    dialects: list[str] = ["postgresql"] if sources else []

    for extractor, dialect_name in (
        (extract_django_migrations, "django"),
        (extract_rails_migrations, "rails"),
        (extract_alembic_migrations, "alembic"),
    ):
        orm_events, orm_sources = extractor(repo_path, migration_directories)
        if orm_sources:
            dialects.append(dialect_name)
            sources.extend(orm_sources)
            _merge_schema_events(tables, relations, indexes, unsupported, orm_events)

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
