import pytest

from aletheore.docs_reference import (
    UNDOCUMENTED,
    build_api_reference,
    build_combined_reference,
    build_endpoints_reference,
    build_module_reference,
    build_schema_reference,
)


def _module(path: str, functions: list[dict], classes: list[dict] | None = None) -> dict:
    return {
        "path": path,
        "language": "python",
        "imports": [],
        "imported_by": [],
        "symbols": {"functions": functions, "classes": classes or []},
    }


def _symbol(name: str, **overrides) -> dict:
    base = {
        "name": name,
        "start_line": 3,
        "end_line": 5,
        "params": "()",
        "docstring": None,
        "return_type": None,
        "is_public": True,
    }
    base.update(overrides)
    return base


def test_build_module_reference_includes_public_symbol_with_docstring_and_citation():
    evidence = {"repository": {"modules": [_module(
        "src/greet.py",
        [_symbol("greet", params="(name: str)", docstring="Return a greeting.", return_type="str", start_line=3)],
    )]}}
    md = build_module_reference(evidence, "src/greet.py")
    assert "greet(name: str) -> str" in md
    assert "Return a greeting." in md
    assert "src/greet.py:3" in md


def test_build_module_reference_marks_missing_docstring_as_undocumented_not_invented():
    evidence = {"repository": {"modules": [_module("src/a.py", [_symbol("f")])]}}
    md = build_module_reference(evidence, "src/a.py")
    assert UNDOCUMENTED in md


def test_build_module_reference_dedents_a_multiline_docstring():
    # A real Python docstring's continuation lines carry the source's own
    # indentation - left as raw text, 4+ leading spaces read as a Markdown
    # code block instead of flowing prose (caught via a real dogfooding run
    # of `aletheore docs` against this repo's own src/aletheore/).
    docstring = "First line.\n    Second line, indented to match source.\n    Third line."
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("f", docstring=docstring)]
    )]}}
    md = build_module_reference(evidence, "src/a.py")
    assert "    Second line" not in md
    assert "Second line, indented to match source." in md


def test_build_module_reference_excludes_private_symbols():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("_helper", is_public=False)]
    )]}}
    md = build_module_reference(evidence, "src/a.py")
    assert "_helper" not in md
    assert "No public symbols found" in md


def test_build_module_reference_raises_for_unknown_module():
    evidence = {"repository": {"modules": []}}
    with pytest.raises(ValueError, match="src/missing.py"):
        build_module_reference(evidence, "src/missing.py")


def test_build_module_reference_includes_public_classes():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [], classes=[_symbol("Widget", docstring="A widget.", params=None)],
    )]}}
    md = build_module_reference(evidence, "src/a.py")
    assert "Widget" in md
    assert "A widget." in md


def test_build_api_reference_returns_one_entry_per_module_with_at_least_one_public_symbol():
    evidence = {"repository": {"modules": [
        _module("src/a.py", [_symbol("f")]),
        _module("src/empty.py", []),
        _module("src/all_private.py", [_symbol("_g", is_public=False)]),
    ]}}
    refs = build_api_reference(evidence)
    assert set(refs) == {"src/a.py"}
    assert "f" in refs["src/a.py"]


def test_build_api_reference_excludes_test_files_even_with_public_symbols():
    # Test functions are module-level and unprefixed, so is_public sees them
    # as ordinary public API - but a Docs page documenting an app's public
    # surface shouldn't include its own test suite, dogfooding-confirmed
    # (test_dashboard.py functions showed up as "generated" API entries).
    evidence = {"repository": {"modules": [
        _module("src/a.py", [_symbol("f")]),
        _module("tests/test_a.py", [_symbol("test_f_does_the_thing")]),
        _module("src/a_test.py", [_symbol("test_g")]),
    ]}}
    refs = build_api_reference(evidence)
    assert set(refs) == {"src/a.py"}


def test_build_module_reference_renders_ai_generated_description_with_marker():
    evidence = {"repository": {"modules": [_module("src/a.py", [_symbol("f", docstring=None)])]}}
    md = build_module_reference(
        evidence, "src/a.py",
        ai_descriptions={"f": {"description": "Does the thing.", "mode": "generated"}},
    )
    assert "Does the thing." in md
    assert "AI-generated" in md
    assert UNDOCUMENTED not in md


def test_build_module_reference_renders_polished_description_with_marker():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("f", docstring="does thing ok")]
    )]}}
    md = build_module_reference(
        evidence, "src/a.py",
        ai_descriptions={"f": {"description": "Does the thing correctly.", "mode": "polished"}},
    )
    assert "Does the thing correctly." in md
    assert "AI-polished" in md
    assert "does thing ok" not in md


def test_build_module_reference_ignores_ai_descriptions_for_symbols_not_in_it():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("f", docstring="Real docstring.")]
    )]}}
    md = build_module_reference(
        evidence, "src/a.py",
        ai_descriptions={"other_symbol": {"description": "x", "mode": "generated"}},
    )
    assert "Real docstring." in md
    assert "AI-generated" not in md
    assert "AI-polished" not in md


def test_build_api_reference_threads_ai_descriptions_by_module():
    evidence = {"repository": {"modules": [
        _module("src/a.py", [_symbol("f", docstring=None)]),
        _module("src/b.py", [_symbol("g", docstring=None)]),
    ]}}
    refs = build_api_reference(evidence, ai_descriptions_by_module={
        "src/a.py": {"f": {"description": "Generated for f.", "mode": "generated"}},
    })
    assert "Generated for f." in refs["src/a.py"]
    assert UNDOCUMENTED in refs["src/b.py"]


def test_build_combined_reference_reports_no_modules():
    md = build_combined_reference({}, "octocat/hello-world")
    assert "octocat/hello-world" in md
    assert "No public functions or classes found yet." in md


def test_build_combined_reference_includes_toc_linking_to_each_module():
    modules = {
        "src/a.py": "# src/a.py\n\n## Functions\n\n### `f()`\n\nDoes a thing.\n",
        "src/b.py": "# src/b.py\n\n## Functions\n\n### `g()`\n\nDoes another thing.\n",
    }
    md = build_combined_reference(modules, "octocat/hello-world")
    assert "[src/a.py](#srcapy)" in md
    assert "[src/b.py](#srcbpy)" in md
    assert "Does a thing." in md
    assert "Does another thing." in md


def test_build_combined_reference_sorts_modules_by_path():
    modules = {
        "src/z.py": "# src/z.py\n\nZ content.\n",
        "src/a.py": "# src/a.py\n\nA content.\n",
    }
    md = build_combined_reference(modules, "octocat/hello-world")
    assert md.index("A content.") < md.index("Z content.")


def _table(name: str, columns: list[dict], **overrides) -> dict:
    base = {"name": name, "columns": columns}
    base.update(overrides)
    return base


def _column(name: str, col_type: str, **overrides) -> dict:
    base = {"name": name, "type": col_type, "primary_key": False, "nullable": True,
            "unique": False, "default": None}
    base.update(overrides)
    return base


def _relation(from_table: str, from_column: str, to_table: str, to_column: str, **overrides) -> dict:
    base = {"from_table": from_table, "from_column": from_column, "to_table": to_table,
            "to_column": to_column, "on_delete": None, "file": "migrations/001.sql", "line": 3}
    base.update(overrides)
    return base


def test_build_schema_reference_returns_empty_string_when_schema_not_checked():
    evidence = {"repository": {"database": {"schema": {"checked": False}}}}
    assert build_schema_reference(evidence) == ""


def test_build_schema_reference_returns_empty_string_when_no_tables():
    evidence = {"repository": {"database": {"schema": {"checked": True, "tables": [], "relations": []}}}}
    assert build_schema_reference(evidence) == ""


def test_build_schema_reference_renders_columns_and_constraints():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("users", [
            _column("id", "BIGSERIAL", primary_key=True, nullable=False),
            _column("email", "TEXT", unique=True, nullable=False),
            _column("bio", "TEXT"),
            _column("credits", "INTEGER", nullable=False, default="0"),
        ])],
        "relations": [],
    }}}}
    md = build_schema_reference(evidence)
    assert "### `users`" in md
    assert "| `id` | BIGSERIAL | PRIMARY KEY, NOT NULL |" in md
    assert "| `email` | TEXT | UNIQUE, NOT NULL |" in md
    assert "| `bio` | TEXT |  |" in md
    assert "| `credits` | INTEGER | NOT NULL, DEFAULT 0 |" in md
    assert "Foreign keys:" not in md


def test_build_schema_reference_renders_foreign_key_relations_with_citation():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("posts", [_column("author_id", "BIGINT", nullable=False)])],
        "relations": [_relation(
            "posts", "author_id", "users", "id",
            on_delete="CASCADE", file="migrations/002_posts.sql", line=5,
        )],
    }}}}
    md = build_schema_reference(evidence)
    assert "Foreign keys:" in md
    assert "`author_id` → `users.id` (`ON DELETE CASCADE`) — `migrations/002_posts.sql:5`" in md


def test_build_schema_reference_relation_without_on_delete_omits_it():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("posts", [_column("author_id", "BIGINT")])],
        "relations": [_relation("posts", "author_id", "users", "id", on_delete=None)],
    }}}}
    md = build_schema_reference(evidence)
    assert "`author_id` → `users.id` — `migrations/001.sql:3`" in md
    assert "ON DELETE" not in md


def test_build_schema_reference_renders_table_location_when_present():
    # Real gap found via audit: schema_map.py's _merge_schema_events always
    # attaches file/line to every table (the location of its own
    # create_table/CREATE TABLE statement, for raw SQL and every ORM
    # source alike), but this renderer discarded it based on the false
    # premise that a table has no real location to cite.
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("users", [_column("id", "BIGSERIAL")], file="db/schema.sql", line=12)],
        "relations": [],
    }}}}
    md = build_schema_reference(evidence)
    assert "### `users` — `db/schema.sql:12`" in md


def test_build_schema_reference_omits_location_when_table_has_none():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("users", [_column("id", "BIGSERIAL")])],
        "relations": [],
    }}}}
    md = build_schema_reference(evidence)
    assert "### `users`" in md
    assert "### `users` —" not in md


def test_build_schema_reference_column_name_with_a_backtick_uses_a_wider_fence():
    # Real Flash Review finding on the PR that introduced backtick
    # "escaping": backslash escapes do not work inside a Markdown code
    # span (CommonMark spec - content between backtick fences is
    # verbatim), so replacing "`" with "\\`" never actually escaped
    # anything - the value's own backtick still closed the span early.
    # The correct fix is a wider fence (more backticks than any run in
    # the value), not backslash escaping.
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("weird", [_column("a`b", "TEXT")])],
        "relations": [],
    }}}}
    md = build_schema_reference(evidence)
    assert "``a`b``" in md
    assert "\\`" not in md


def test_build_schema_reference_column_name_with_a_double_backtick_run_uses_a_triple_fence():
    # The fence must be wider than the LONGEST run of consecutive
    # backticks in the value, not just wider than a single backtick - two
    # backticks in a row would still close a two-backtick fence early.
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("weird", [_column("a``b", "TEXT")])],
        "relations": [],
    }}}}
    md = build_schema_reference(evidence)
    assert "```a``b```" in md


def test_build_schema_reference_sorts_tables_alphabetically():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("zebras", [_column("id", "INT")]), _table("apples", [_column("id", "INT")])],
        "relations": [],
    }}}}
    md = build_schema_reference(evidence)
    assert md.index("### `apples`") < md.index("### `zebras`")


def test_build_schema_reference_escapes_pipe_characters_in_plain_cell_text():
    # Type/constraints cells are plain text, not a code span - a literal
    # "|" there genuinely does need backslash escaping to avoid breaking
    # the table row into extra columns.
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("weird", [_column("id", "ENUM('x'|'y')")])],
        "relations": [],
    }}}}
    md = build_schema_reference(evidence)
    assert "ENUM('x'\\|'y')" in md


def test_build_schema_reference_column_name_with_a_pipe_needs_no_escaping():
    # A column name is rendered inside a code span (_code_span) - GitHub's
    # table renderer treats inline code as atomic when splitting a row
    # into cells, so a raw "|" here does not need (and must not receive)
    # backslash escaping, unlike the plain-text type/constraints cells.
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("weird", [_column("a|b", "TEXT")])],
        "relations": [],
    }}}}
    md = build_schema_reference(evidence)
    assert "`a|b`" in md


def test_build_endpoints_reference_returns_empty_string_when_not_checked():
    evidence = {"repository": {"api_endpoints": {"checked": False}}}
    assert build_endpoints_reference(evidence) == ""


def test_build_endpoints_reference_returns_empty_string_when_no_resolved_endpoints():
    evidence = {"repository": {"api_endpoints": {"checked": True, "endpoints": [
        {"method": "GET", "path": None, "unresolved": True, "file": "a.py", "line": 1, "handler": "h"},
    ]}}}
    assert build_endpoints_reference(evidence) == ""


def test_build_endpoints_reference_excludes_unresolved_endpoints():
    evidence = {"repository": {"api_endpoints": {"checked": True, "endpoints": [
        {"method": "GET", "path": "/known", "unresolved": False, "file": "a.py", "line": 1, "handler": "h"},
        {"method": "GET", "path": None, "unresolved": True, "file": "b.py", "line": 2, "handler": "dynamic"},
    ]}}}
    md = build_endpoints_reference(evidence)
    assert "/known" in md
    assert "dynamic" not in md


def test_build_endpoints_reference_renders_method_path_handler_and_citation():
    evidence = {"repository": {"api_endpoints": {"checked": True, "endpoints": [
        {"method": "POST", "path": "/users/{id}", "unresolved": False,
         "file": "app/routes.py", "line": 42, "handler": "update_user"},
    ]}}}
    md = build_endpoints_reference(evidence)
    assert "| POST | `/users/{id}` | `update_user` | `app/routes.py:42` |" in md


def test_build_endpoints_reference_sorts_by_path_then_method():
    evidence = {"repository": {"api_endpoints": {"checked": True, "endpoints": [
        {"method": "POST", "path": "/b", "unresolved": False, "file": "a.py", "line": 1, "handler": "h"},
        {"method": "GET", "path": "/a", "unresolved": False, "file": "a.py", "line": 2, "handler": "h"},
        {"method": "GET", "path": "/b", "unresolved": False, "file": "a.py", "line": 3, "handler": "h"},
    ]}}}
    md = build_endpoints_reference(evidence)
    assert md.index("/a") < md.index("GET | `/b`") < md.index("POST | `/b`")


def test_build_combined_reference_without_evidence_omits_overview_sections():
    # Backward compatibility: the default (no evidence) reproduces the
    # exact prior output with no overview sections.
    modules = {"src/a.py": "# src/a.py\n\nContent.\n"}
    md = build_combined_reference(modules, "octocat/hello-world")
    assert "API Endpoints" not in md
    assert "Database Schema" not in md


def test_build_combined_reference_with_evidence_adds_overview_sections_before_modules():
    modules = {"src/a.py": "# src/a.py\n\nModule content.\n"}
    evidence = {"repository": {
        "database": {"schema": {
            "checked": True,
            "tables": [_table("users", [_column("id", "INT")])],
            "relations": [],
        }},
        "api_endpoints": {"checked": True, "endpoints": [
            {"method": "GET", "path": "/x", "unresolved": False, "file": "a.py", "line": 1, "handler": "h"},
        ]},
    }}
    md = build_combined_reference(modules, "octocat/hello-world", evidence)
    assert "[API Endpoints](#api-endpoints)" in md
    assert "[Database Schema](#database-schema)" in md
    assert md.index("## API Endpoints") < md.index("## Database Schema") < md.index("# src/a.py")


def test_build_combined_reference_with_evidence_but_no_schema_or_endpoints_omits_sections():
    modules = {"src/a.py": "# src/a.py\n\nContent.\n"}
    evidence = {"repository": {
        "database": {"schema": {"checked": False}},
        "api_endpoints": {"checked": False},
    }}
    md = build_combined_reference(modules, "octocat/hello-world", evidence)
    assert "API Endpoints" not in md
    assert "Database Schema" not in md


def test_build_combined_reference_renders_overview_sections_even_with_no_modules():
    # A repo with real schema/endpoints but zero documented public symbols
    # must not fall into the "No public functions" empty-state message.
    evidence = {"repository": {
        "database": {"schema": {
            "checked": True,
            "tables": [_table("users", [_column("id", "INT")])],
            "relations": [],
        }},
        "api_endpoints": {"checked": False},
    }}
    md = build_combined_reference({}, "octocat/hello-world", evidence)
    assert "Database Schema" in md
    assert "No public functions or classes found yet." not in md


def test_build_schema_reference_renders_a_relation_whose_from_table_was_never_scanned():
    # Real bug found via audit: build_schema_reference only visited a
    # relation via relations_by_table.get(table["name"], []) inside the
    # per-table loop - a relation whose from_table has no matching entry
    # in schema["tables"] was never visited at all and vanished from the
    # document with no trace. Realistically reachable: schema_map.py's
    # ALTER TABLE handling emits an add_relation event keyed on the ALTER
    # TABLE's own target table with no requirement that a CREATE TABLE for
    # that same table was ever parsed in this scan (e.g. the table
    # predates the tracked migration directory).
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("users", [_column("id", "INT", primary_key=True)])],
        "relations": [_relation("user_roles", "user_id", "users", "id")],
    }}}}
    md = build_schema_reference(evidence)
    assert "### `user_roles`" in md
    assert "`user_id` → `users.id`" in md


def test_build_schema_reference_orphan_table_relations_still_sorted_by_column():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [_table("users", [_column("id", "INT", primary_key=True)])],
        "relations": [
            _relation("audit_log", "target_id", "widgets", "id"),
            _relation("audit_log", "actor_id", "users", "id"),
        ],
    }}}}
    md = build_schema_reference(evidence)
    assert md.index("actor_id") < md.index("target_id")


def test_build_combined_reference_deduplicates_colliding_anchors():
    # Real bug found via audit: _github_heading_anchor strips punctuation
    # before collapsing whitespace, so two distinct module paths that
    # differ only in stripped characters slugify to the identical anchor
    # ("src/a.b.py" and "src/ab.py" both become "srcabpy") - the TOC
    # produced two links pointing at the same anchor, so one always jumped
    # to the wrong heading. GitHub's own renderer instead assigns the bare
    # anchor to the first heading and appends -1, -2, ... to each later
    # colliding one; this must match that so a generated TOC link always
    # lands on the same heading GitHub itself would land on.
    modules = {
        "src/a.b.py": "# src/a.b.py\n\nA-dot-b content.\n",
        "src/ab.py": "# src/ab.py\n\nAb content.\n",
    }
    md = build_combined_reference(modules, "octocat/hello-world")
    assert "[src/a.b.py](#srcabpy)" in md
    assert "[src/ab.py](#srcabpy-1)" in md
