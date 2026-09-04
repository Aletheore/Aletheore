"""ORM-native migration parsing: Django, Rails, and Alembic.

`schema_map.py`'s `extract_schema` only ever reads raw `.sql` files. Django
migrations are `.py`, Rails migrations are `.rb`, Alembic migrations are
`.py` too - none of them are `.sql`, so a repository using any of these
three (all three are explicitly recognized ORMs in `scanner/detect.py`'s
`DB_ORM_MARKERS_PY`, and their migration directories are hardcoded special
cases in `_detect_migration_directories`) previously produced a schema
that silently looked identical to "no schema at all."

This module produces the same event shapes `schema_map.py`'s replay loop
already merges (`create_table` / `add_column` / `create_index` /
`remove_column` / `alter_column` / `rename_column` / `remove_table` /
`rename_table` / `remove_index` / `raw_sql` / `unsupported`), but
pre-built rather than raw SQL text - each ORM's own call syntax is walked
directly with tree-sitter rather than routed through the SQL tokenizer,
since none of these three is SQL. The one exception is `raw_sql`: Django's
`RunSQL`, Alembic's `op.execute`, and Rails' `execute` all accept a literal
SQL string, which is handed to schema_map.py's own Postgres parser and
replayed exactly like a real `.sql` file's statement - not re-implemented
here.

Scope, deliberately narrower than each framework's full DSL, mirroring
schema_map.py's own restraint: model every operation with a clear,
unambiguous DDL meaning; record anything else as `unsupported` rather
than guess. What's still `unsupported`, and why each one specifically:

- Django: `RunPython` (arbitrary code - not statically modelable),
  `AlterModelOptions`/`AlterUniqueTogether`/`AlterModelTable` (legacy
  APIs superseded by `Meta.constraints`/per-field `db_column`, rare in
  real migrations), `ManyToManyField` (an implicit through-table this
  module does not construct). Everything else - `CreateModel`, `AddField`,
  `RemoveField`, `AlterField`, `RenameField`, `DeleteModel`, `RenameModel`,
  `AddIndex`, `RemoveIndex`, `RunSQL` - is modeled, including the implicit
  `id` primary key Django adds when no field is marked `primary_key=True`.
  Table names follow Django's default `<app_label>_<model>` convention
  (app_label taken from the migration file's own app directory); an
  explicit `Meta.db_table` override on `CreateModel` itself is not read.
- Rails: `create_join_table` (the implicit join-table name follows a
  real but fiddlier alphabetical-pluralization rule not worth the added
  surface) and `remove_foreign_key` (relations are not individually
  addressable for removal without risking dropping the wrong one when a
  table has more than one FK to the same target) are recorded, not
  modeled. Everything else - `create_table`/`change_table` blocks,
  `t.<type>`/`t.references`/`t.belongs_to`/`t.timestamps`, standalone
  `add_column`/`remove_column`/`rename_column`/`change_column`/
  `change_column_null`/`change_column_default`/`add_index`/`remove_index`/
  `add_foreign_key`/`drop_table`/`rename_table`/`execute` - is modeled.
  Any Ruby method call that isn't a recognized Rails migration DSL method
  (`_RAILS_KNOWN_METHODS`) is silently ignored rather than flagged, so a
  seed-data helper or a `reversible`/`say` wrapper doesn't drown real
  findings in noise. Pluralization (for `references`'/`belongs_to`'s
  implied target table) is a simple regular-noun heuristic, not a full
  inflector - irregular plurals (`person` -> `people`) resolve wrong.
- Alembic: `op.drop_constraint` (constraint names are not tracked
  precisely enough on relations to resolve which one to remove) is
  recorded, not modeled. Everything else - `op.create_table`,
  `op.add_column`, `op.create_index`, `op.create_foreign_key` (plus an
  inline `sa.ForeignKey(...)`/`sa.ForeignKeyConstraint(...)` inside
  either), `op.drop_table`, `op.drop_column`, `op.alter_column`,
  `op.drop_index`, `op.rename_table`, `op.execute` - is modeled. Only
  statements inside `def upgrade():` are read; `downgrade()` is ignored.
  Alembic orders migrations via each file's own `revision`/`down_revision`
  chain, not filename - this module does not resolve that graph and
  instead sorts files by path like every other migration source, which is
  a known, documented approximation, not the guaranteed real replay order.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter import Node, Parser

from aletheore.scanner.graph import PY_LANGUAGE, RUBY_LANGUAGE

_DJANGO_SNIFF_MARKERS = (b"django.db", b"migrations.Migration")

_DJANGO_PK_FIELD_TYPES = {"AUTOFIELD", "BIGAUTOFIELD", "SMALLAUTOFIELD"}
_DJANGO_RELATION_FIELD_TYPES = {"FOREIGNKEY", "ONETOONEFIELD"}
# Genuinely unmodelable (RunPython: arbitrary code) or metadata-only with no
# DB-shape effect (AlterModelOptions covers things like `ordering`/
# `verbose_name`; AlterUniqueTogether/AlterModelTable are legacy Django <2.2
# APIs superseded by Meta.constraints/db_table on the field itself, rare
# enough in real migrations that modeling them is not worth the added
# surface - still recorded, not silently dropped).
_DJANGO_UNSUPPORTED_OPS = {
    "AlterModelOptions", "AlterUniqueTogether", "RunPython", "AlterModelTable",
}

_RAILS_TYPE_METHODS = {
    "string", "text", "integer", "bigint", "float", "decimal", "numeric",
    "datetime", "timestamp", "time", "date", "binary", "boolean", "json",
    "jsonb", "uuid",
}
# create_join_table's implicit table name follows a real but fiddlier rule
# (the two model names, alphabetically sorted, pluralized, underscore-
# joined) - real and common enough to name explicitly rather than silently
# drop, not common enough to be worth the added surface here.
_RAILS_UNSUPPORTED_METHODS = {"create_join_table"}
# The full set of recognized Rails migration DSL methods (modeled ones plus
# the unsupported ones above) - anything else encountered is ordinary Ruby
# (a seed-data helper, `reversible`/`up_only` wrappers, `say`, application
# code) and must be silently ignored, not flagged, or every migration file
# would drown in false "unsupported" noise for non-schema calls.
_RAILS_KNOWN_METHODS = _RAILS_UNSUPPORTED_METHODS | {
    "create_table", "add_column", "add_index", "add_foreign_key",
    "remove_column", "drop_table", "rename_column", "rename_table",
    "change_column", "remove_index", "change_table", "remove_foreign_key",
    "change_column_null", "change_column_default", "execute",
}

_ALEMBIC_UNSUPPORTED_OPS = {"drop_constraint"}


def _py_parser() -> Parser:
    parser = Parser()
    parser.language = PY_LANGUAGE
    return parser


def _rb_parser() -> Parser:
    parser = Parser()
    parser.language = RUBY_LANGUAGE
    return parser


def looks_like_django_migration(source: bytes) -> bool:
    return any(marker in source for marker in _DJANGO_SNIFF_MARKERS)


def django_app_label(rel_path: str) -> str:
    """`<app>/migrations/0001_initial.py` -> `app`."""
    parts = Path(rel_path).parts
    try:
        idx = parts.index("migrations")
    except ValueError:
        return parts[0] if parts else ""
    return parts[idx - 1] if idx > 0 else ""


def _django_table_name(app_label: str, model_name: str) -> str:
    return f"{app_label}_{model_name.lower()}"


# ---------------------------------------------------------------------------
# Shared Python call/argument helpers (Django + Alembic)
# ---------------------------------------------------------------------------


def _py_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _py_string_text(node: Node, source: bytes) -> str | None:
    if node.type != "string":
        return None
    content = next((c for c in node.children if c.type == "string_content"), None)
    if content is None:
        return ""
    return source[content.start_byte : content.end_byte].decode(errors="replace")


def _py_call_name(call: Node, source: bytes) -> str | None:
    """The trailing name of a call's function - `sa.Column(...)` -> `Column`,
    `create_table(...)` -> `create_table`."""
    func = call.child_by_field_name("function")
    if func is None:
        return None
    if func.type == "attribute":
        attr = func.child_by_field_name("attribute")
        return _py_text(attr, source) if attr is not None else None
    if func.type == "identifier":
        return _py_text(func, source)
    return None


def _py_call_receiver(call: Node, source: bytes) -> str | None:
    func = call.child_by_field_name("function")
    if func is None or func.type != "attribute":
        return None
    obj = func.child_by_field_name("object")
    return _py_text(obj, source) if obj is not None else None


def _py_args(call: Node) -> list[Node]:
    args = call.child_by_field_name("arguments")
    if args is None:
        return []
    return list(args.named_children)


def _py_positional(args: list[Node]) -> list[Node]:
    return [a for a in args if a.type != "keyword_argument"]


def _py_kwarg(args: list[Node], name: str, source: bytes) -> Node | None:
    for arg in args:
        if arg.type != "keyword_argument":
            continue
        name_node = arg.child_by_field_name("name")
        if name_node is not None and _py_text(name_node, source) == name:
            return arg.child_by_field_name("value")
    return None


def _py_bool_kwarg(args: list[Node], name: str, source: bytes) -> bool:
    value = _py_kwarg(args, name, source)
    return value is not None and value.type == "true"


def _py_walk_calls(root: Node):
    """Every `call` node under `root`, not descending into nested
    `function_definition`s (used to keep Alembic's upgrade()/downgrade()
    bodies separate)."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "call":
            yield node
        for child in reversed(node.children):
            stack.append(child)


def _py_scalar_default(node: Node, source: bytes) -> str | None:
    if node.type == "none":
        return None
    text = _py_text(node, source)
    return text


# ---------------------------------------------------------------------------
# Django
# ---------------------------------------------------------------------------


def _django_resolve_target_table(target_text: str, app_label: str, current_model: str) -> str:
    """`to=` on a ForeignKey/OneToOneField is always a string in Django
    migrations (never a live class reference, to avoid app-loading-order
    issues during replay): "self" (same model), "Model" (same app), or
    "app_label.Model" (cross-app)."""
    if target_text.lower() == "self":
        return _django_table_name(app_label, current_model)
    if "." in target_text:
        target_app, target_model = target_text.split(".", 1)
        return _django_table_name(target_app, target_model)
    return _django_table_name(app_label, target_text)


def _django_column_from_field(
    field_name: str, field_call: Node, source: bytes, rel_path: str, line: int,
    app_label: str = "", current_model: str = "",
) -> tuple[dict, dict | None]:
    field_type = _py_call_name(field_call, source) or "UNKNOWN"
    args = _py_args(field_call)

    column = {
        "name": field_name,
        "type": field_type.upper(),
        "primary_key": _py_bool_kwarg(args, "primary_key", source),
        "nullable": _py_bool_kwarg(args, "null", source),
        "unique": _py_bool_kwarg(args, "unique", source),
        "default": None,
        "file": rel_path,
        "line": line,
    }
    default_node = _py_kwarg(args, "default", source)
    if default_node is not None:
        column["default"] = _py_scalar_default(default_node, source)
    if column["primary_key"]:
        column["nullable"] = False

    relation = None
    if field_type.upper() in _DJANGO_RELATION_FIELD_TYPES:
        target = _py_kwarg(args, "to", source)
        if target is None:
            positional = _py_positional(args)
            target = positional[0] if positional else None
        target_raw = _py_string_text(target, source) if target is not None else None
        target_text = (
            _django_resolve_target_table(target_raw, app_label, current_model)
            if target_raw
            else None
        )
        on_delete_node = _py_kwarg(args, "on_delete", source)
        on_delete = None
        if on_delete_node is not None:
            on_delete_name = (
                on_delete_node.child_by_field_name("attribute")
                if on_delete_node.type == "attribute"
                else None
            )
            on_delete = _py_text(
                on_delete_name if on_delete_name is not None else on_delete_node, source
            ).upper()
        column["name"] = f"{field_name}_id"
        relation = {
            "from_column": column["name"],
            "to_table": target_text,
            "to_column": "id",
            "on_delete": on_delete,
            "file": rel_path,
            "line": line,
        }

    return column, relation


def _django_model_operations(
    operations_list: Node, source: bytes, rel_path: str, app_label: str
) -> list[dict]:
    events: list[dict] = []

    for op_call in operations_list.named_children:
        if op_call.type != "call":
            continue
        op_name = _py_call_name(op_call, source)
        line = op_call.start_point[0] + 1
        args = _py_args(op_call)

        if op_name == "CreateModel":
            name_node = _py_kwarg(args, "name", source)
            model_name = _py_string_text(name_node, source) if name_node else None
            fields_node = _py_kwarg(args, "fields", source)
            if model_name is None or fields_node is None or fields_node.type != "list":
                continue
            table = _django_table_name(app_label, model_name)
            columns: list[dict] = []
            relations: list[dict] = []
            has_pk = False
            for field_tuple in fields_node.named_children:
                if field_tuple.type != "tuple" or len(field_tuple.named_children) < 2:
                    continue
                fname_node, fcall_node = field_tuple.named_children[0], field_tuple.named_children[1]
                fname = _py_string_text(fname_node, source)
                if fname is None or fcall_node.type != "call":
                    continue
                column, relation = _django_column_from_field(
                    fname, fcall_node, source, rel_path, field_tuple.start_point[0] + 1,
                    app_label=app_label, current_model=model_name,
                )
                if column["primary_key"] or (_py_call_name(fcall_node, source) or "").upper() in _DJANGO_PK_FIELD_TYPES:
                    has_pk = True
                columns.append(column)
                if relation is not None:
                    relations.append(relation)
            if not has_pk:
                columns.insert(
                    0,
                    {
                        "name": "id", "type": "AUTOFIELD", "primary_key": True,
                        "nullable": False, "unique": True, "default": None,
                        "file": rel_path, "line": line,
                    },
                )
            events.append(
                {
                    "kind": "create_table", "table": table, "file": rel_path, "line": line,
                    "columns": columns, "relations": relations,
                }
            )
            continue

        if op_name == "AddField":
            model_node = _py_kwarg(args, "model_name", source)
            name_node = _py_kwarg(args, "name", source)
            field_node = _py_kwarg(args, "field", source)
            model_name = _py_string_text(model_node, source) if model_node else None
            fname = _py_string_text(name_node, source) if name_node else None
            if model_name is None or fname is None or field_node is None or field_node.type != "call":
                continue
            table = _django_table_name(app_label, model_name)
            column, relation = _django_column_from_field(
                fname, field_node, source, rel_path, line,
                app_label=app_label, current_model=model_name,
            )
            events.append(
                {"kind": "add_column", "table": table, "file": rel_path, "line": line,
                 "column": column, "relation": relation}
            )
            continue

        if op_name == "AddIndex":
            model_node = _py_kwarg(args, "model_name", source)
            index_node = _py_kwarg(args, "index", source)
            model_name = _py_string_text(model_node, source) if model_node else None
            if model_name is None or index_node is None or index_node.type != "call":
                continue
            table = _django_table_name(app_label, model_name)
            index_args = _py_args(index_node)
            fields_node = _py_kwarg(index_args, "fields", source)
            field_names = []
            if fields_node is not None and fields_node.type == "list":
                for f in fields_node.named_children:
                    text = _py_string_text(f, source)
                    if text:
                        field_names.append(text)
            name_node = _py_kwarg(index_args, "name", source)
            index_name = _py_string_text(name_node, source) if name_node else None
            if not index_name:
                index_name = f"{table}_{'_'.join(field_names) or 'idx'}_idx"
            events.append(
                {"kind": "create_index", "table": table, "name": index_name,
                 "columns": field_names, "unique": False, "file": rel_path, "line": line}
            )
            continue

        if op_name == "RemoveField":
            model_node = _py_kwarg(args, "model_name", source)
            name_node = _py_kwarg(args, "name", source)
            model_name = _py_string_text(model_node, source) if model_node else None
            fname = _py_string_text(name_node, source) if name_node else None
            if model_name is None or fname is None:
                continue
            events.append(
                {"kind": "remove_column", "table": _django_table_name(app_label, model_name),
                 "name": fname, "file": rel_path, "line": line}
            )
            continue

        if op_name == "AlterField":
            model_node = _py_kwarg(args, "model_name", source)
            name_node = _py_kwarg(args, "name", source)
            field_node = _py_kwarg(args, "field", source)
            model_name = _py_string_text(model_node, source) if model_node else None
            fname = _py_string_text(name_node, source) if name_node else None
            if model_name is None or fname is None or field_node is None or field_node.type != "call":
                continue
            table = _django_table_name(app_label, model_name)
            column, relation = _django_column_from_field(
                fname, field_node, source, rel_path, line,
                app_label=app_label, current_model=model_name,
            )
            events.append(
                {"kind": "alter_column", "table": table, "name": column["name"],
                 "changes": {
                     "type": column["type"], "nullable": column["nullable"],
                     "unique": column["unique"], "default": column["default"],
                 },
                 "file": rel_path, "line": line}
            )
            if relation is not None:
                events.append({"kind": "add_relation", "table": table, "relation": relation,
                                "file": rel_path, "line": line})
            continue

        if op_name == "RenameField":
            model_node = _py_kwarg(args, "model_name", source)
            old_node = _py_kwarg(args, "old_name", source)
            new_node = _py_kwarg(args, "new_name", source)
            model_name = _py_string_text(model_node, source) if model_node else None
            old_name = _py_string_text(old_node, source) if old_node else None
            new_name = _py_string_text(new_node, source) if new_node else None
            if model_name is None or old_name is None or new_name is None:
                continue
            events.append(
                {"kind": "rename_column", "table": _django_table_name(app_label, model_name),
                 "old_name": old_name, "new_name": new_name, "file": rel_path, "line": line}
            )
            continue

        if op_name == "DeleteModel":
            name_node = _py_kwarg(args, "name", source)
            if name_node is None:
                positional = _py_positional(args)
                name_node = positional[0] if positional else None
            model_name = _py_string_text(name_node, source) if name_node else None
            if model_name is None:
                continue
            events.append(
                {"kind": "remove_table", "table": _django_table_name(app_label, model_name)}
            )
            continue

        if op_name == "RenameModel":
            old_node = _py_kwarg(args, "old_name", source)
            new_node = _py_kwarg(args, "new_name", source)
            old_name = _py_string_text(old_node, source) if old_node else None
            new_name = _py_string_text(new_node, source) if new_node else None
            if old_name is None or new_name is None:
                continue
            events.append(
                {"kind": "rename_table",
                 "old_table": _django_table_name(app_label, old_name),
                 "new_table": _django_table_name(app_label, new_name)}
            )
            continue

        if op_name == "RemoveIndex":
            model_node = _py_kwarg(args, "model_name", source)
            name_node = _py_kwarg(args, "name", source)
            model_name = _py_string_text(model_node, source) if model_node else None
            index_name = _py_string_text(name_node, source) if name_node else None
            if model_name is None or index_name is None:
                continue
            events.append(
                {"kind": "remove_index", "table": _django_table_name(app_label, model_name),
                 "name": index_name}
            )
            continue

        if op_name == "RunSQL":
            sql_node = _py_kwarg(args, "sql", source)
            if sql_node is None:
                positional = _py_positional(args)
                sql_node = positional[0] if positional else None
            if sql_node is None:
                continue
            # `sql=` is a single string or a list of (query,) / (query, params)
            # entries - RunSQL replays them in order, same as separate
            # statements in one .sql file would.
            statements: list[str] = []
            if sql_node.type == "string":
                text = _py_string_text(sql_node, source)
                if text:
                    statements.append(text)
            elif sql_node.type == "list":
                for entry in sql_node.named_children:
                    target = entry.named_children[0] if entry.type == "tuple" and entry.named_children else entry
                    text = _py_string_text(target, source)
                    if text:
                        statements.append(text)
            if statements:
                events.append(
                    {"kind": "raw_sql", "sql": "\n".join(statements), "file": rel_path, "line": line}
                )
            continue

        if op_name in _DJANGO_UNSUPPORTED_OPS:
            events.append(
                {"kind": "unsupported", "file": rel_path, "line": line,
                 "statement": f"migrations.{op_name}(...) not modeled"}
            )

    return events


def extract_django_migrations(
    repo_path: Path, migration_directories: list[str]
) -> tuple[list[dict], list[str]]:
    """Every Django-style `.py` migration under the given directories.

    Only directories that both match the generic `migrations` marker (not
    `alembic/versions` or `db/migrate`, which are handled separately) and
    whose files actually look like Django migrations are read - a
    `migrations/` directory belonging to some other tool should not be
    mis-parsed as Django.
    """
    events: list[dict] = []
    sources: list[str] = []
    parser = _py_parser()
    files: list[Path] = []
    for directory in migration_directories:
        if directory in ("alembic/versions", "db/migrate"):
            continue
        base = repo_path / directory
        if not base.is_dir():
            continue
        for candidate in base.glob("*.py"):
            if candidate.is_symlink() or not candidate.is_file() or candidate.name == "__init__.py":
                continue
            files.append(candidate)

    for path in sorted(files, key=lambda p: p.relative_to(repo_path).as_posix()):
        rel_path = path.relative_to(repo_path).as_posix()
        try:
            source = path.read_bytes()
        except OSError:
            continue
        if not looks_like_django_migration(source):
            continue
        sources.append(rel_path)
        app_label = django_app_label(rel_path)
        tree = parser.parse(source)
        # `operations = [...]` is a module-level assignment inside the
        # Migration class body, found by walking assignments rather than
        # calls.
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type == "assignment":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if (
                    left is not None and left.type == "identifier"
                    and _py_text(left, source) == "operations"
                    and right is not None and right.type == "list"
                ):
                    events.extend(_django_model_operations(right, source, rel_path, app_label))
                    continue
            stack.extend(reversed(node.children))

    return events, sources


# ---------------------------------------------------------------------------
# Alembic
# ---------------------------------------------------------------------------


def _alembic_column_from_call(
    col_call: Node, source: bytes, rel_path: str, line: int
) -> tuple[dict | None, dict | None]:
    args = _py_args(col_call)
    positional = _py_positional(args)
    if not positional:
        return None, None
    name = _py_string_text(positional[0], source)
    if name is None:
        return None, None

    col_type = "UNKNOWN"
    relation = None
    for arg in positional[1:]:
        if arg.type != "call":
            continue
        call_name = _py_call_name(arg, source) or ""
        if call_name == "ForeignKey":
            fk_args = _py_positional(_py_args(arg))
            target = _py_string_text(fk_args[0], source) if fk_args else None
            if target and "." in target:
                to_table, to_column = target.rsplit(".", 1)
                relation = {
                    "from_column": name, "to_table": to_table, "to_column": to_column,
                    "on_delete": None, "file": rel_path, "line": line,
                }
        elif col_type == "UNKNOWN":
            col_type = call_name.upper()

    column = {
        "name": name, "type": col_type,
        "primary_key": _py_bool_kwarg(args, "primary_key", source),
        "nullable": not _py_bool_kwarg(args, "primary_key", source),
        "unique": _py_bool_kwarg(args, "unique", source),
        "default": None,
        "file": rel_path, "line": line,
    }
    nullable_kw = _py_kwarg(args, "nullable", source)
    if nullable_kw is not None:
        column["nullable"] = nullable_kw.type == "true"
    default_node = _py_kwarg(args, "server_default", source) or _py_kwarg(args, "default", source)
    if default_node is not None:
        column["default"] = _py_scalar_default(default_node, source)

    return column, relation


def _alembic_upgrade_events(upgrade_body: Node, source: bytes, rel_path: str) -> list[dict]:
    events: list[dict] = []

    for call in _py_walk_calls(upgrade_body):
        receiver = _py_call_receiver(call, source)
        if receiver != "op":
            continue
        op_name = _py_call_name(call, source)
        line = call.start_point[0] + 1
        args = _py_args(call)
        positional = _py_positional(args)

        if op_name == "create_table":
            if not positional:
                continue
            table = _py_string_text(positional[0], source)
            if table is None:
                continue
            columns: list[dict] = []
            relations: list[dict] = []
            has_pk = False
            for member in positional[1:]:
                if member.type != "call":
                    continue
                member_name = _py_call_name(member, source)
                if member_name == "Column":
                    column, relation = _alembic_column_from_call(member, source, rel_path, line)
                    if column is not None:
                        if column["primary_key"]:
                            has_pk = True
                        columns.append(column)
                    if relation is not None:
                        relations.append({**relation, "from_column": relation["from_column"]})
                elif member_name == "ForeignKeyConstraint":
                    fk_args = _py_positional(_py_args(member))
                    if len(fk_args) >= 2 and fk_args[0].type == "list" and fk_args[1].type == "list":
                        local_cols = [
                            _py_string_text(c, source) for c in fk_args[0].named_children
                        ]
                        remote_cols = [
                            _py_string_text(c, source) for c in fk_args[1].named_children
                        ]
                        for local_col, remote in zip(local_cols, remote_cols):
                            if not local_col or not remote or "." not in remote:
                                continue
                            to_table, to_column = remote.rsplit(".", 1)
                            relations.append(
                                {"from_column": local_col, "to_table": to_table,
                                 "to_column": to_column, "on_delete": None,
                                 "file": rel_path, "line": line}
                            )
            if not has_pk:
                columns.insert(
                    0,
                    {"name": "id", "type": "INTEGER", "primary_key": True, "nullable": False,
                     "unique": True, "default": None, "file": rel_path, "line": line},
                )
            events.append(
                {"kind": "create_table", "table": table, "file": rel_path, "line": line,
                 "columns": columns, "relations": relations}
            )
            continue

        if op_name == "add_column":
            if len(positional) < 2 or positional[1].type != "call":
                continue
            table = _py_string_text(positional[0], source)
            column, relation = _alembic_column_from_call(positional[1], source, rel_path, line)
            if table is None or column is None:
                continue
            events.append(
                {"kind": "add_column", "table": table, "file": rel_path, "line": line,
                 "column": column, "relation": relation}
            )
            continue

        if op_name == "create_index":
            if len(positional) < 2:
                continue
            index_name = _py_string_text(positional[0], source)
            table = _py_string_text(positional[1], source)
            cols_node = positional[2] if len(positional) > 2 else None
            columns = []
            if cols_node is not None and cols_node.type == "list":
                columns = [c for c in (_py_string_text(n, source) for n in cols_node.named_children) if c]
            unique = _py_bool_kwarg(args, "unique", source)
            if index_name and table:
                events.append(
                    {"kind": "create_index", "table": table, "name": index_name,
                     "columns": columns, "unique": unique, "file": rel_path, "line": line}
                )
            continue

        if op_name == "create_foreign_key":
            if len(positional) < 5:
                continue
            source_table = _py_string_text(positional[1], source)
            target_table = _py_string_text(positional[2], source)
            local_cols_node, remote_cols_node = positional[3], positional[4]
            if (
                source_table and target_table
                and local_cols_node.type == "list" and remote_cols_node.type == "list"
            ):
                local_cols = [_py_string_text(c, source) for c in local_cols_node.named_children]
                remote_cols = [_py_string_text(c, source) for c in remote_cols_node.named_children]
                ondelete_node = _py_kwarg(args, "ondelete", source)
                on_delete = _py_string_text(ondelete_node, source) if ondelete_node else None
                for local_col, remote_col in zip(local_cols, remote_cols):
                    if not local_col or not remote_col:
                        continue
                    events.append(
                        {"kind": "add_relation", "table": source_table, "file": rel_path, "line": line,
                         "relation": {"from_column": local_col, "to_table": target_table,
                                      "to_column": remote_col,
                                      "on_delete": on_delete.upper() if on_delete else None,
                                      "file": rel_path, "line": line}}
                    )
            continue

        if op_name == "drop_table":
            if not positional:
                continue
            table = _py_string_text(positional[0], source)
            if table:
                events.append({"kind": "remove_table", "table": table})
            continue

        if op_name == "drop_column":
            if len(positional) < 2:
                continue
            table = _py_string_text(positional[0], source)
            col_name = _py_string_text(positional[1], source)
            if table and col_name:
                events.append(
                    {"kind": "remove_column", "table": table, "name": col_name,
                     "file": rel_path, "line": line}
                )
            continue

        if op_name == "alter_column":
            if len(positional) < 2:
                continue
            table = _py_string_text(positional[0], source)
            col_name = _py_string_text(positional[1], source)
            if not table or not col_name:
                continue
            changes: dict = {}
            type_kw = _py_kwarg(args, "type_", source)
            if type_kw is not None and type_kw.type == "call":
                changes["type"] = (_py_call_name(type_kw, source) or "UNKNOWN").upper()
            nullable_kw = _py_kwarg(args, "nullable", source)
            if nullable_kw is not None:
                changes["nullable"] = nullable_kw.type == "true"
            default_kw = _py_kwarg(args, "server_default", source)
            if default_kw is not None:
                changes["default"] = _py_scalar_default(default_kw, source)
            if changes:
                events.append(
                    {"kind": "alter_column", "table": table, "name": col_name,
                     "changes": changes, "file": rel_path, "line": line}
                )
            continue

        if op_name == "drop_index":
            if not positional:
                continue
            index_name = _py_string_text(positional[0], source)
            table_kw = _py_kwarg(args, "table_name", source)
            table = _py_string_text(table_kw, source) if table_kw else (
                _py_string_text(positional[1], source) if len(positional) > 1 else None
            )
            if index_name and table:
                events.append({"kind": "remove_index", "table": table, "name": index_name})
            continue

        if op_name == "rename_table":
            if len(positional) < 2:
                continue
            old_table = _py_string_text(positional[0], source)
            new_table = _py_string_text(positional[1], source)
            if old_table and new_table:
                events.append(
                    {"kind": "rename_table", "old_table": old_table, "new_table": new_table}
                )
            continue

        if op_name == "execute":
            if not positional:
                continue
            text = _py_string_text(positional[0], source)
            if text:
                events.append({"kind": "raw_sql", "sql": text, "file": rel_path, "line": line})
            continue

        if op_name in _ALEMBIC_UNSUPPORTED_OPS:
            events.append(
                {"kind": "unsupported", "file": rel_path, "line": line,
                 "statement": f"op.{op_name}(...) not modeled"}
            )

    return events


def extract_alembic_migrations(
    repo_path: Path, migration_directories: list[str]
) -> tuple[list[dict], list[str]]:
    events: list[dict] = []
    sources: list[str] = []
    parser = _py_parser()
    for directory in migration_directories:
        if directory != "alembic/versions" and not directory.endswith("/alembic/versions"):
            continue
        base = repo_path / directory
        if not base.is_dir():
            continue
        files = sorted(
            (f for f in base.glob("*.py") if f.is_file() and not f.is_symlink()),
            key=lambda p: p.relative_to(repo_path).as_posix(),
        )
        for path in files:
            rel_path = path.relative_to(repo_path).as_posix()
            try:
                source = path.read_bytes()
            except OSError:
                continue
            sources.append(rel_path)
            tree = parser.parse(source)
            stack = list(reversed(tree.root_node.children))
            while stack:
                node = stack.pop()
                if node.type == "function_definition":
                    name_node = node.child_by_field_name("name")
                    if name_node is not None and _py_text(name_node, source) == "upgrade":
                        body = node.child_by_field_name("body")
                        if body is not None:
                            events.extend(_alembic_upgrade_events(body, source, rel_path))
                    continue
                stack.extend(reversed(node.children))
    return events, sources


# ---------------------------------------------------------------------------
# Rails
# ---------------------------------------------------------------------------


def _rb_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode(errors="replace")


def _rb_symbol_text(node: Node, source: bytes) -> str | None:
    if node.type == "simple_symbol":
        return _rb_text(node, source).lstrip(":")
    if node.type == "string":
        content = next((c for c in node.children if c.type == "string_content"), None)
        return source[content.start_byte : content.end_byte].decode(errors="replace") if content else ""
    return None


def _rb_call_name(call: Node, source: bytes) -> str | None:
    method = call.child_by_field_name("method")
    return _rb_text(method, source) if method is not None else None


def _rb_args(call: Node) -> list[Node]:
    args = call.child_by_field_name("arguments")
    if args is None:
        return []
    return list(args.named_children)


def _rb_kwarg(args: list[Node], name: str, source: bytes) -> Node | None:
    for arg in args:
        if arg.type != "pair":
            continue
        key = arg.child_by_field_name("key")
        if key is None:
            continue
        key_text = _rb_text(key, source).lstrip(":")
        if key_text == name:
            return arg.child_by_field_name("value")
    return None


def _rb_bool_kwarg(args: list[Node], name: str, source: bytes) -> bool | None:
    value = _rb_kwarg(args, name, source)
    if value is None:
        return None
    return value.type == "true"


_IRREGULAR_SUFFIXES = (("y", "ies"), ("s", "ses"), ("x", "xes"), ("ch", "ches"), ("sh", "shes"))


def _pluralize(word: str) -> str:
    """A regular-noun heuristic, not a real inflector - see module docstring."""
    lower = word.lower()
    for suffix, replacement in _IRREGULAR_SUFFIXES:
        if lower.endswith(suffix) and suffix != "s":
            return lower[: -len(suffix)] + replacement
    if lower.endswith("s"):
        return lower
    return lower + "s"


def _rails_column_from_typed_call(
    call: Node, source: bytes, rel_path: str, line: int
) -> tuple[dict | None, dict | None]:
    method = _rb_call_name(call, source)
    args = _rb_args(call)
    positional = [a for a in args if a.type != "pair"]

    if method in ("references", "belongs_to"):
        if not positional:
            return None, None
        ref_name = _rb_symbol_text(positional[0], source)
        if not ref_name:
            return None, None
        fk_flag = _rb_bool_kwarg(args, "foreign_key", source)
        column = {
            "name": f"{ref_name}_id", "type": "BIGINT", "primary_key": False,
            "nullable": _rb_bool_kwarg(args, "null", source) is not False,
            "unique": False, "default": None, "file": rel_path, "line": line,
        }
        relation = None
        if fk_flag is not False:
            relation = {
                "from_column": column["name"], "to_table": _pluralize(ref_name),
                "to_column": "id", "on_delete": None, "file": rel_path, "line": line,
            }
        return column, relation

    if method not in _RAILS_TYPE_METHODS:
        return None, None
    if not positional:
        return None, None
    col_name = _rb_symbol_text(positional[0], source)
    if not col_name:
        return None, None
    null_kw = _rb_bool_kwarg(args, "null", source)
    default_node = _rb_kwarg(args, "default", source)
    column = {
        "name": col_name, "type": method.upper(), "primary_key": False,
        "nullable": null_kw is not False,
        "unique": _rb_bool_kwarg(args, "unique", source) is True,
        "default": _rb_text(default_node, source) if default_node is not None else None,
        "file": rel_path, "line": line,
    }
    return column, None


def _rails_create_table_events(call: Node, source: bytes, rel_path: str) -> list[dict]:
    args = _rb_args(call)
    positional = [a for a in args if a.type != "pair"]
    if not positional:
        return []
    table = _rb_symbol_text(positional[0], source)
    if not table:
        return []
    line = call.start_point[0] + 1
    id_disabled = _rb_bool_kwarg(args, "id", source) is False

    columns: list[dict] = []
    relations: list[dict] = []
    if not id_disabled:
        columns.append(
            {"name": "id", "type": "BIGINT", "primary_key": True, "nullable": False,
             "unique": True, "default": None, "file": rel_path, "line": line}
        )

    do_block = call.child_by_field_name("block") or next(
        (c for c in call.children if c.type == "do_block"), None
    )
    if do_block is not None:
        body = do_block.child_by_field_name("body")
        inner_calls = body.named_children if body is not None else []
        for inner in inner_calls:
            if inner.type != "call" or _rb_call_name(inner, source) == "":
                continue
            method = _rb_call_name(inner, source)
            if method == "timestamps":
                for tcol in ("created_at", "updated_at"):
                    columns.append(
                        {"name": tcol, "type": "DATETIME", "primary_key": False,
                         "nullable": False, "unique": False, "default": None,
                         "file": rel_path, "line": inner.start_point[0] + 1}
                    )
                continue
            column, relation = _rails_column_from_typed_call(
                inner, source, rel_path, inner.start_point[0] + 1
            )
            if column is not None:
                columns.append(column)
            if relation is not None:
                relations.append(relation)

    return [
        {"kind": "create_table", "table": table, "file": rel_path, "line": line,
         "columns": columns, "relations": relations}
    ]


def _rails_change_table_events(call: Node, source: bytes, rel_path: str) -> list[dict]:
    """`change_table :table do |t| ... end` - the same `t.<type>`/
    `t.references`/`t.timestamps` calls create_table's block accepts, plus
    `t.remove`/`t.rename`, applied to an existing table rather than a new
    one."""
    args = _rb_args(call)
    positional = [a for a in args if a.type != "pair"]
    if not positional:
        return []
    table = _rb_symbol_text(positional[0], source)
    if not table:
        return []

    events: list[dict] = []
    do_block = call.child_by_field_name("block") or next(
        (c for c in call.children if c.type == "do_block"), None
    )
    if do_block is None:
        return []
    body = do_block.child_by_field_name("body")
    inner_calls = body.named_children if body is not None else []
    for inner in inner_calls:
        if inner.type != "call":
            continue
        method = _rb_call_name(inner, source)
        inner_line = inner.start_point[0] + 1
        inner_args = _rb_args(inner)
        inner_positional = [a for a in inner_args if a.type != "pair"]

        if method == "remove":
            for target in inner_positional:
                col_name = _rb_symbol_text(target, source)
                if col_name:
                    events.append(
                        {"kind": "remove_column", "table": table, "name": col_name,
                         "file": rel_path, "line": inner_line}
                    )
            continue
        if method == "rename":
            if len(inner_positional) >= 2:
                old_name = _rb_symbol_text(inner_positional[0], source)
                new_name = _rb_symbol_text(inner_positional[1], source)
                if old_name and new_name:
                    events.append(
                        {"kind": "rename_column", "table": table, "old_name": old_name,
                         "new_name": new_name, "file": rel_path, "line": inner_line}
                    )
            continue
        if method == "index":
            if inner_positional:
                col_name = _rb_symbol_text(inner_positional[0], source)
                if col_name:
                    events.append(
                        {"kind": "create_index", "table": table,
                         "name": f"index_{table}_on_{col_name}", "columns": [col_name],
                         "unique": _rb_bool_kwarg(inner_args, "unique", source) is True,
                         "file": rel_path, "line": inner_line}
                    )
            continue
        if method == "timestamps":
            for tcol in ("created_at", "updated_at"):
                events.append(
                    {"kind": "add_column", "table": table, "file": rel_path, "line": inner_line,
                     "column": {"name": tcol, "type": "DATETIME", "primary_key": False,
                                "nullable": False, "unique": False, "default": None,
                                "file": rel_path, "line": inner_line},
                     "relation": None}
                )
            continue
        column, relation = _rails_column_from_typed_call(inner, source, rel_path, inner_line)
        if column is not None:
            events.append(
                {"kind": "add_column", "table": table, "file": rel_path, "line": inner_line,
                 "column": column, "relation": relation}
            )

    return events


def _rails_top_level_events(call: Node, source: bytes, rel_path: str) -> list[dict]:
    method = _rb_call_name(call, source)
    line = call.start_point[0] + 1
    args = _rb_args(call)
    positional = [a for a in args if a.type != "pair"]

    if method == "add_column":
        if len(positional) < 3:
            return []
        table = _rb_symbol_text(positional[0], source)
        col_name = _rb_symbol_text(positional[1], source)
        col_type = _rb_symbol_text(positional[2], source)
        if not table or not col_name or not col_type:
            return []
        null_kw = _rb_bool_kwarg(args, "null", source)
        default_node = _rb_kwarg(args, "default", source)
        column = {
            "name": col_name, "type": col_type.upper(), "primary_key": False,
            "nullable": null_kw is not False,
            "unique": _rb_bool_kwarg(args, "unique", source) is True,
            "default": _rb_text(default_node, source) if default_node is not None else None,
            "file": rel_path, "line": line,
        }
        return [{"kind": "add_column", "table": table, "file": rel_path, "line": line,
                 "column": column, "relation": None}]

    if method == "add_index":
        if len(positional) < 2:
            return []
        table = _rb_symbol_text(positional[0], source)
        cols_node = positional[1]
        if cols_node.type == "array":
            columns = [c for c in (_rb_symbol_text(n, source) for n in cols_node.named_children) if c]
        else:
            single = _rb_symbol_text(cols_node, source)
            columns = [single] if single else []
        if not table or not columns:
            return []
        name_kw = _rb_kwarg(args, "name", source)
        index_name = _rb_symbol_text(name_kw, source) if name_kw else f"index_{table}_on_{'_'.join(columns)}"
        return [{"kind": "create_index", "table": table, "name": index_name,
                 "columns": columns, "unique": _rb_bool_kwarg(args, "unique", source) is True,
                 "file": rel_path, "line": line}]

    if method == "add_foreign_key":
        if len(positional) < 2:
            return []
        from_table = _rb_symbol_text(positional[0], source)
        to_table = _rb_symbol_text(positional[1], source)
        if not from_table or not to_table:
            return []
        col_kw = _rb_kwarg(args, "column", source)
        from_column = _rb_symbol_text(col_kw, source) if col_kw else f"{to_table[:-1]}_id"
        on_delete_kw = _rb_kwarg(args, "on_delete", source)
        on_delete = _rb_symbol_text(on_delete_kw, source) if on_delete_kw else None
        return [{"kind": "add_relation", "table": from_table, "file": rel_path, "line": line,
                 "relation": {"from_column": from_column, "to_table": to_table, "to_column": "id",
                              "on_delete": on_delete.upper() if on_delete else None,
                              "file": rel_path, "line": line}}]

    if method == "remove_column":
        if len(positional) < 2:
            return []
        table = _rb_symbol_text(positional[0], source)
        col_name = _rb_symbol_text(positional[1], source)
        if not table or not col_name:
            return []
        return [{"kind": "remove_column", "table": table, "name": col_name,
                 "file": rel_path, "line": line}]

    if method == "drop_table":
        if not positional:
            return []
        table = _rb_symbol_text(positional[0], source)
        return [{"kind": "remove_table", "table": table}] if table else []

    if method == "rename_column":
        if len(positional) < 3:
            return []
        table = _rb_symbol_text(positional[0], source)
        old_name = _rb_symbol_text(positional[1], source)
        new_name = _rb_symbol_text(positional[2], source)
        if not table or not old_name or not new_name:
            return []
        return [{"kind": "rename_column", "table": table, "old_name": old_name,
                 "new_name": new_name, "file": rel_path, "line": line}]

    if method == "rename_table":
        if len(positional) < 2:
            return []
        old_table = _rb_symbol_text(positional[0], source)
        new_table = _rb_symbol_text(positional[1], source)
        return [{"kind": "rename_table", "old_table": old_table, "new_table": new_table}] \
            if old_table and new_table else []

    if method == "change_column":
        if len(positional) < 3:
            return []
        table = _rb_symbol_text(positional[0], source)
        col_name = _rb_symbol_text(positional[1], source)
        col_type = _rb_symbol_text(positional[2], source)
        if not table or not col_name or not col_type:
            return []
        null_kw = _rb_bool_kwarg(args, "null", source)
        changes = {"type": col_type.upper()}
        if null_kw is not None:
            changes["nullable"] = null_kw
        return [{"kind": "alter_column", "table": table, "name": col_name, "changes": changes,
                 "file": rel_path, "line": line}]

    if method == "remove_index":
        if not positional:
            return []
        table = _rb_symbol_text(positional[0], source)
        name_kw = _rb_kwarg(args, "name", source)
        if name_kw is not None:
            index_name = _rb_symbol_text(name_kw, source)
        elif len(positional) > 1:
            col_kw = positional[1]
            col_name = _rb_symbol_text(col_kw, source)
            index_name = f"index_{table}_on_{col_name}" if col_name else None
        else:
            column_kw = _rb_kwarg(args, "column", source)
            col_name = _rb_symbol_text(column_kw, source) if column_kw else None
            index_name = f"index_{table}_on_{col_name}" if col_name else None
        if not table or not index_name:
            return []
        return [{"kind": "remove_index", "table": table, "name": index_name}]

    if method == "change_table":
        return _rails_change_table_events(call, source, rel_path)

    if method == "remove_foreign_key":
        if len(positional) < 2:
            return []
        from_table = _rb_symbol_text(positional[0], source)
        to_table = _rb_symbol_text(positional[1], source)
        if not from_table or not to_table:
            return []
        return [{"kind": "unsupported", "file": rel_path, "line": line,
                 "statement": f"remove_foreign_key({from_table}, {to_table}) not modeled "
                               "(relations are not individually addressable for removal)"}]

    if method in ("change_column_null", "change_column_default"):
        if len(positional) < 3:
            return []
        table = _rb_symbol_text(positional[0], source)
        col_name = _rb_symbol_text(positional[1], source)
        if not table or not col_name:
            return []
        value_node = positional[2]
        if method == "change_column_null":
            changes = {"nullable": value_node.type == "true"}
        else:
            changes = {"default": _rb_text(value_node, source)}
        return [{"kind": "alter_column", "table": table, "name": col_name, "changes": changes,
                 "file": rel_path, "line": line}]

    if method == "execute":
        if not positional:
            return []
        text = _rb_symbol_text(positional[0], source)
        return [{"kind": "raw_sql", "sql": text, "file": rel_path, "line": line}] if text else []

    if method not in _RAILS_KNOWN_METHODS:
        return []

    return [{"kind": "unsupported", "file": rel_path, "line": line,
             "statement": f"{method}(...) not modeled"}]


def extract_rails_migrations(
    repo_path: Path, migration_directories: list[str]
) -> tuple[list[dict], list[str]]:
    events: list[dict] = []
    sources: list[str] = []
    parser = _rb_parser()
    for directory in migration_directories:
        if directory != "db/migrate" and not directory.endswith("/db/migrate"):
            continue
        base = repo_path / directory
        if not base.is_dir():
            continue
        files = sorted(
            (f for f in base.glob("*.rb") if f.is_file() and not f.is_symlink()),
            key=lambda p: p.relative_to(repo_path).as_posix(),
        )
        for path in files:
            rel_path = path.relative_to(repo_path).as_posix()
            try:
                source = path.read_bytes()
            except OSError:
                continue
            sources.append(rel_path)
            tree = parser.parse(source)
            # A plain (non-reversed) push+pop here would visit sibling
            # statements in reverse source order - confirmed via a real
            # multi-statement `change` block (rename_column, rename_table,
            # drop_table all in one method): events came out drop/rename/
            # rename instead of source order, silently reordering replay.
            stack = [tree.root_node]
            while stack:
                node = stack.pop()
                if node.type == "call":
                    name = _rb_call_name(node, source)
                    if name == "create_table":
                        events.extend(_rails_create_table_events(node, source, rel_path))
                        continue
                    if name == "change_table":
                        events.extend(_rails_change_table_events(node, source, rel_path))
                        continue
                    events.extend(_rails_top_level_events(node, source, rel_path))
                stack.extend(reversed(node.children))
    return events, sources
