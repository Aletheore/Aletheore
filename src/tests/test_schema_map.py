import json
from pathlib import Path

from aletheore.schema_map import extract_schema, skipped_schema
from aletheore.wiki_diagrams import build_schema_diagram


def write_migrations(tmp_path: Path, files: dict[str, str]) -> Path:
    migrations = tmp_path / "migrations"
    migrations.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (migrations / name).write_text(body)
    return tmp_path


def test_extracts_tables_columns_and_constraints(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001_init.sql": """
            CREATE TABLE IF NOT EXISTS installations (
                installation_id BIGINT PRIMARY KEY,
                account_login   TEXT NOT NULL,
                plan            TEXT NOT NULL DEFAULT 'free'
            );
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert [t["name"] for t in result["tables"]] == ["installations"]
    columns = {c["name"]: c for c in result["tables"][0]["columns"]}
    assert columns["installation_id"]["type"] == "BIGINT"
    assert columns["installation_id"]["primary_key"] is True
    # PRIMARY KEY implies NOT NULL in Postgres even when unstated - recording
    # it as nullable would make the diagram contradict the database.
    assert columns["installation_id"]["nullable"] is False
    assert columns["account_login"]["nullable"] is False
    assert columns["plan"]["default"] == "'free'"


def test_replays_alter_table_add_column_in_filename_order(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001_init.sql": "CREATE TABLE t (id BIGINT PRIMARY KEY);",
            "010_later.sql": "ALTER TABLE t ADD COLUMN added_last TEXT;",
            "002_earlier.sql": "ALTER TABLE t ADD COLUMN added_first INT NOT NULL DEFAULT 0;",
        },
    )
    result = extract_schema(repo, ["migrations"])
    columns = [c["name"] for c in result["tables"][0]["columns"]]

    # Zero-padded names sort lexically into real migration order, so 002
    # must land before 010 regardless of directory iteration order.
    assert columns == ["id", "added_first", "added_last"]
    assert result["tables"][0]["columns"][1]["file"] == "migrations/002_earlier.sql"


def test_output_is_byte_identical_across_runs(tmp_path):
    """The scanner's whole promise. Migrations replay sequentially, so their
    order *is* the schema - filesystem ordering leaking in would change
    content, not just presentation."""
    repo = write_migrations(
        tmp_path,
        {
            "001_a.sql": "CREATE TABLE a (id BIGINT PRIMARY KEY);",
            "002_b.sql": "CREATE TABLE b (id BIGINT PRIMARY KEY, a_id BIGINT REFERENCES a(id));",
            "003_c.sql": "CREATE TABLE c (id BIGINT PRIMARY KEY);",
        },
    )
    runs = {json.dumps(extract_schema(repo, ["migrations"]), sort_keys=True) for _ in range(5)}
    assert len(runs) == 1


def test_extracts_foreign_keys_with_on_delete(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE parents (id BIGINT PRIMARY KEY);
            CREATE TABLE children (
                id        BIGSERIAL PRIMARY KEY,
                parent_id BIGINT NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
                other_id  BIGINT REFERENCES parents(id) ON DELETE SET NULL
            );
            """
        },
    )
    relations = extract_schema(repo, ["migrations"])["relations"]

    assert len(relations) == 2
    by_column = {r["from_column"]: r for r in relations}
    assert by_column["parent_id"]["to_table"] == "parents"
    assert by_column["parent_id"]["to_column"] == "id"
    assert by_column["parent_id"]["on_delete"] == "CASCADE"
    assert by_column["other_id"]["on_delete"] == "SET NULL"


def test_table_level_foreign_key_constraint_is_captured(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE parents (id BIGINT PRIMARY KEY);
            CREATE TABLE children (
                id BIGSERIAL PRIMARY KEY,
                parent_id BIGINT NOT NULL,
                FOREIGN KEY (parent_id) REFERENCES parents(id) ON DELETE CASCADE
            );
            """
        },
    )
    result = extract_schema(repo, ["migrations"])
    children = next(t for t in result["tables"] if t["name"] == "children")

    # The constraint defines no column of its own, but it does define an edge.
    assert [c["name"] for c in children["columns"]] == ["id", "parent_id"]
    assert result["relations"][0]["from_table"] == "children"
    assert result["relations"][0]["from_column"] == "parent_id"
    assert result["relations"][0]["to_table"] == "parents"


def test_references_inside_a_comment_is_not_a_relation(tmp_path):
    """Found on the real repo: a naive grep counted 43 REFERENCES where only
    42 were real, because one sat inside a `--` comment explaining why the
    column deliberately has no foreign key."""
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            -- installation_id is deliberately a bare BIGINT with no REFERENCES
            /* nor this one: REFERENCES elsewhere(id) */
            CREATE TABLE t (installation_id BIGINT NOT NULL);
            """
        },
    )
    assert extract_schema(repo, ["migrations"])["relations"] == []


def test_create_table_if_not_exists_does_not_duplicate_columns(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": "CREATE TABLE IF NOT EXISTS t (id BIGINT PRIMARY KEY);",
            "002.sql": "CREATE TABLE IF NOT EXISTS t (id BIGINT PRIMARY KEY, extra TEXT);",
        },
    )
    result = extract_schema(repo, ["migrations"])

    # A no-op in Postgres, so a no-op here - re-applying would double every column.
    assert len(result["tables"]) == 1
    assert [c["name"] for c in result["tables"][0]["columns"]] == ["id"]


def test_semicolons_inside_literals_do_not_split_statements(tmp_path):
    repo = write_migrations(
        tmp_path,
        {"001.sql": "CREATE TABLE t (id BIGINT PRIMARY KEY, note TEXT DEFAULT 'a;b');"},
    )
    result = extract_schema(repo, ["migrations"])

    assert [c["name"] for c in result["tables"][0]["columns"]] == ["id", "note"]
    assert result["tables"][0]["columns"][1]["default"] == "'a;b'"


def test_commas_inside_a_type_do_not_split_columns(tmp_path):
    repo = write_migrations(
        tmp_path, {"001.sql": "CREATE TABLE t (amount NUMERIC(10, 2), label DOUBLE PRECISION);"}
    )
    columns = extract_schema(repo, ["migrations"])["tables"][0]["columns"]

    assert [c["name"] for c in columns] == ["amount", "label"]
    assert columns[0]["type"] == "NUMERIC(10,_2)".replace("_", " ")
    assert columns[1]["type"] == "DOUBLE PRECISION"


def test_non_ddl_statements_are_recorded_not_parsed(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id BIGINT PRIMARY KEY);
            UPDATE t SET id = 1 WHERE id = 2;
            CREATE VIEW v AS SELECT * FROM t;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert len(result["tables"]) == 1
    statements = [u["statement"] for u in result["unsupported"]]
    assert any(s.startswith("UPDATE") for s in statements)
    assert any(s.startswith("CREATE VIEW") for s in statements)


def test_alter_on_an_unknown_table_is_recorded_not_invented(tmp_path):
    """A squashed baseline or a table created outside these directories -
    inventing a one-column table would render a phantom node in the diagram."""
    repo = write_migrations(tmp_path, {"001.sql": "ALTER TABLE ghost ADD COLUMN x TEXT;"})
    result = extract_schema(repo, ["migrations"])

    assert result["tables"] == []
    assert "unknown table" in result["unsupported"][0]["statement"]


def test_malformed_sql_never_raises(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": "CREATE TABLE unbalanced (id BIGINT",
            "002.sql": "CREATE TABLE 'weird quoted; NOT SQL AT ALL @@@ ;;;",
            "003.sql": "/* unterminated block comment",
            "004.sql": "CREATE TABLE fine (id BIGINT PRIMARY KEY);",
        },
    )
    result = extract_schema(repo, ["migrations"])

    # Degrades per statement, never per scan.
    assert "fine" in [t["name"] for t in result["tables"]]


def test_indexes_are_extracted(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id BIGINT PRIMARY KEY, a TEXT, b TEXT);
            CREATE INDEX IF NOT EXISTS t_a_idx ON t (a);
            CREATE UNIQUE INDEX t_ab_idx ON t (a, b);
            """
        },
    )
    indexes = extract_schema(repo, ["migrations"])["indexes"]

    assert [(i["name"], i["unique"], i["columns"]) for i in indexes] == [
        ("t_a_idx", False, ["a"]),
        ("t_ab_idx", True, ["a", "b"]),
    ]


def test_symlinked_migrations_are_not_followed(tmp_path):
    repo = write_migrations(tmp_path, {"001.sql": "CREATE TABLE real (id BIGINT PRIMARY KEY);"})
    outside = tmp_path / "outside.sql"
    outside.write_text("CREATE TABLE smuggled (id BIGINT PRIMARY KEY);")
    (repo / "migrations" / "002_link.sql").symlink_to(outside)

    tables = [t["name"] for t in extract_schema(repo, ["migrations"])["tables"]]
    assert tables == ["real"]


def test_skipped_schema_has_the_same_keys_as_a_real_one(tmp_path):
    """The gating contract: the section is always present with identical
    keys, so no consumer ever branches on whether it exists - only on
    `checked`."""
    repo = write_migrations(tmp_path, {"001.sql": "CREATE TABLE t (id BIGINT PRIMARY KEY);"})

    assert set(skipped_schema("requires a paid plan")) == set(extract_schema(repo, ["migrations"]))
    assert skipped_schema("requires a paid plan")["checked"] is False


def test_diagram_is_none_when_the_section_was_not_checked():
    evidence = {"repository": {"database": {"schema": skipped_schema("requires a paid plan")}}}
    assert build_schema_diagram(evidence) is None


def test_diagram_renders_entities_and_relations(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE parents (id BIGINT PRIMARY KEY);
            CREATE TABLE children (
                id BIGSERIAL PRIMARY KEY,
                parent_id BIGINT NOT NULL REFERENCES parents(id) ON DELETE CASCADE
            );
            """
        },
    )
    evidence = {"repository": {"database": {"schema": extract_schema(repo, ["migrations"])}}}
    diagram = build_schema_diagram(evidence)

    assert diagram.startswith("erDiagram")
    assert "BIGINT parent_id FK" in diagram
    assert "parents ||--|| children" in diagram
    # ON DELETE CASCADE is an identifying relationship; anything else is not.
    assert build_schema_diagram(evidence) == diagram
