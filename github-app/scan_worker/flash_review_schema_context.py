"""Schema/endpoint-aware review context for Flash Review.

Real, deterministic facts about what a changed migration or route-defining
file actually does - not a risk finding (see flash_review.py's
attach_risk_evidence, whose _risk() shape assumes an already-identified
concern with a severity). A migration dropping a column, or a file
defining a route, is neutral until the reviewing model reasons about it
against the rest of the diff; this module's job is only to make those
real facts available, grounded and citable, the same way
build_dependency_impact_context exposes raw import/imported_by facts.

The schema side needs no before/after diffing: a migration file's own
content IS the mutation statement (`ALTER TABLE x DROP COLUMN y` already
says, structurally, "this removes a column"), so re-parsing just the
changed file's new content through the same extractors that built
`repository.database.schema` in the first place is sufficient - no
historical snapshot access required. Endpoint removal detection (comparing
old vs. new file content) is deliberately out of scope for this module;
it needs a separate old-content fetch this module's callers don't
currently provide.
"""

from aletheore.orm_migrations import (
    alembic_events_from_source,
    django_events_from_source,
    looks_like_django_migration,
    rails_events_from_source,
)
from aletheore.schema_map import sql_events_from_text

MAX_SCHEMA_ENDPOINT_BYTES = 20_000
MAX_EVENTS_PER_FILE = 20
MAX_ENDPOINTS_PER_FILE = 20

# schema.dialect stores the display form ("postgresql"), sql_events_from_text
# wants sqlglot's read= form ("postgres") - every other dialect string is
# already identical between the two.
_DIALECT_DISPLAY_TO_SQL = {"postgresql": "postgres"}
_KNOWN_SQL_DIALECTS = {"postgres", "mysql", "sqlite", "tsql", "oracle"}


def _sql_dialect_for(evidence: dict) -> str:
    dialects = evidence.get("repository", {}).get("database", {}).get("schema", {}).get("dialect") or []
    for dialect in dialects:
        mapped = _DIALECT_DISPLAY_TO_SQL.get(dialect, dialect)
        if mapped in _KNOWN_SQL_DIALECTS:
            return mapped
    return "postgres"


def _is_migration_file(evidence: dict, file_path: str) -> bool:
    directories = [
        entry.get("path")
        for entry in evidence.get("repository", {}).get("database", {}).get("migration_directories", [])
        if entry.get("path")
    ]
    return any(
        file_path == directory or file_path.startswith(directory.rstrip("/") + "/")
        for directory in directories
    )


def _resolve_raw_sql_events(events: list[dict], sql_dialect: str) -> tuple[list[dict], list[dict]]:
    """Rails' `execute`, Django's `RunSQL`, and Alembic's `op.execute` all
    surface as a `raw_sql` event carrying literal SQL text, not a
    structural fact on its own - schema_map.py's own full-scan merge
    (_merge_schema_events) already re-parses that text through the SQL
    extractor rather than leaving it opaque, and this does the same, so a
    real migration using the raw-SQL escape hatch for something the ORM
    DSL doesn't cover directly (confirmed real and common: a real
    Discourse migration used `execute "ALTER SEQUENCE ... AS bigint"`)
    isn't silently invisible here just because it didn't go through
    add_column/remove_column/etc. directly. Returns (events, unsupported):
    unsupported carries real statement text for SQL the extractor can
    tokenize but not model (GRANT/REVOKE, raw DML, etc.) - schema_map.py's
    own merge keeps these rather than dropping them, so this does too."""
    resolved: list[dict] = []
    unsupported: list[dict] = []
    for event in events:
        if event.get("kind") == "raw_sql":
            sql_text = event.get("sql")
            if sql_text:
                sql_events, sql_unsupported = sql_events_from_text(sql_text, event["file"], dialect=sql_dialect)
                resolved.extend(sql_events)
                unsupported.extend(sql_unsupported)
            continue
        resolved.append(event)
    return resolved, unsupported


def _migration_events_for_file(
    file_path: str, content: str, sql_dialect: str
) -> tuple[list[dict], list[dict]]:
    """Real DDL events this one changed file's new content produces, and
    any unsupported (tokenized but not modeled) statements alongside them
    - dispatched by extension, then (for .py, which Django and Alembic
    both use) by the same content sniff orm_migrations.py's own directory
    scan uses, so a file this module treats as Django/Alembic is
    identified the same way a real scan would identify it. Django is
    checked before the Alembic content sniff: `_DJANGO_SNIFF_MARKERS`
    (`django.db`, `migrations.Migration`) only matches real Django
    migration boilerplate, while the Alembic check is a bare substring
    test for `down_revision` that a Django file could contain incidentally
    (a comment, a string, an unrelated variable) - checking the more
    specific marker first avoids misrouting a Django file to the Alembic
    parser.
    """
    source = content.encode("utf-8", errors="replace")
    if file_path.endswith(".sql"):
        return sql_events_from_text(content, file_path, dialect=sql_dialect)
    if file_path.endswith(".rb"):
        return _resolve_raw_sql_events(rails_events_from_source(source, file_path), sql_dialect)
    if file_path.endswith(".py"):
        if looks_like_django_migration(source):
            return _resolve_raw_sql_events(django_events_from_source(source, file_path), sql_dialect)
        if b"down_revision" in source:
            return _resolve_raw_sql_events(alembic_events_from_source(source, file_path), sql_dialect)
    return [], []


def _summarize_event(event: dict) -> str | None:
    """One human-readable line for a real schema event, or None for an
    event kind with nothing citable/structural to say (raw_sql, unsupported -
    already recorded elsewhere with their own real text, not re-summarized
    here to avoid presenting an opaque blob as if it were a clean fact)."""
    kind = event.get("kind")
    table = event.get("table")
    if kind == "create_table":
        columns = ", ".join(c["name"] for c in (event.get("columns") or [])[:10])
        return f"CREATE TABLE {table} ({columns})"
    if kind == "add_column":
        column = event.get("column") or {}
        return f"ADD COLUMN {table}.{column.get('name')} ({column.get('type')})"
    if kind == "remove_column":
        return f"DROP COLUMN {table}.{event.get('name')}"
    if kind == "rename_column":
        return f"RENAME COLUMN {table}.{event.get('old_name')} -> {event.get('new_name')}"
    if kind == "remove_table":
        return f"DROP TABLE {table}"
    if kind == "rename_table":
        return f"RENAME TABLE {event.get('old_name')} -> {event.get('new_name')}"
    if kind == "add_relation":
        relation = event.get("relation") or {}
        return (
            f"ADD FOREIGN KEY {table}.{relation.get('from_column')} "
            f"-> {relation.get('to_table')}.{relation.get('to_column')}"
        )
    if kind == "remove_relation":
        return f"DROP CONSTRAINT {event.get('name')} on {table}"
    if kind == "create_index":
        columns = ", ".join(event.get("columns") or [])
        return f"CREATE INDEX {event.get('name')} ON {table} ({columns})"
    if kind == "remove_index":
        return f"DROP INDEX {event.get('name')}"
    if kind == "drop_primary_key":
        return f"DROP PRIMARY KEY on {table}"
    if kind == "alter_column":
        changes = event.get("changes") or {}
        changed = ", ".join(f"{key}={value}" for key, value in changes.items() if value is not None)
        return f"ALTER COLUMN {table}.{event.get('name')} ({changed})" if changed else None
    return None


def _summarize_unsupported(entry: dict) -> str:
    """A tokenized-but-not-modeled statement, with its real text - the
    same "recorded as unsupported, never silently dropped" fact
    schema_map.py's own full-scan merge exposes via
    `repository.database.schema.unsupported`, so a migration's raw SQL
    that the extractor can't structurally model (GRANT/REVOKE, raw DML,
    an unrecognized DDL shape) is still visible to the reviewing model
    instead of vanishing without a trace."""
    return f"UNSUPPORTED (not modeled, real text): {entry.get('statement')}"


def build_schema_endpoint_context(
    evidence: dict | None, changed_files: list[str], file_contents: dict[str, str]
) -> str:
    """Deterministic schema/endpoint facts for changed files, formatted as
    review context - not conclusions. `file_contents` is the same
    path->new-content lookup flash_review.py already builds
    (fetch_review_file_context), so this needs no new fetch."""
    if not evidence:
        return ""

    sql_dialect = _sql_dialect_for(evidence)
    endpoints_by_file: dict[str, list[dict]] = {}
    for endpoint in evidence.get("repository", {}).get("api_endpoints", {}).get("endpoints", []) or []:
        if endpoint.get("file") and not endpoint.get("unresolved"):
            endpoints_by_file.setdefault(endpoint["file"], []).append(endpoint)

    lines: list[str] = []

    def emit(line: str) -> bool:
        # Checks the real encoded size of the final joined output (header
        # + every line + the "\n" separators between them), not just this
        # line's own bytes - a running total of line bytes alone under-
        # counts by the header and every separator, letting the actual
        # returned context exceed MAX_SCHEMA_ENDPOINT_BYTES by that much.
        candidate = lines + [line]
        if len(_joined(candidate).encode("utf-8")) > MAX_SCHEMA_ENDPOINT_BYTES:
            return False
        lines.append(line)
        return True

    for file_path in changed_files:
        if _is_migration_file(evidence, file_path):
            content = file_contents.get(file_path)
            if content is not None:
                events, unsupported = _migration_events_for_file(file_path, content, sql_dialect)
                summaries = [s for s in (_summarize_event(e) for e in events) if s]
                summaries += [_summarize_unsupported(u) for u in unsupported]
                if summaries:
                    if not emit(f"{file_path} is a migration - real schema changes it makes:"):
                        return _joined(lines)
                    for summary in summaries[:MAX_EVENTS_PER_FILE]:
                        if not emit(f"  - {summary}"):
                            return _joined(lines)

        file_endpoints = endpoints_by_file.get(file_path)
        if file_endpoints:
            if not emit(f"{file_path} currently defines these API endpoints:"):
                return _joined(lines)
            for endpoint in file_endpoints[:MAX_ENDPOINTS_PER_FILE]:
                line = (
                    f"  - {endpoint['method']} {endpoint['path']} -> "
                    f"{endpoint.get('handler')} ({file_path}:{endpoint.get('line')})"
                )
                if not emit(line):
                    return _joined(lines)

    return _joined(lines)


def _joined(lines: list[str]) -> str:
    if not lines:
        return ""
    return "--- deterministic schema/endpoint facts for changed files (not conclusions) ---\n" + "\n".join(lines)
