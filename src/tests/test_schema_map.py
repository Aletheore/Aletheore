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
    # sqlglot normalizes NUMERIC to its exact Postgres synonym DECIMAL -
    # same type, different canonical spelling.
    assert columns[0]["type"] == "DECIMAL(10,_2)".replace("_", " ")
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


def test_a_comment_inside_a_column_list_does_not_swallow_the_next_column(tmp_path):
    # Batch 5 finding 1: neither _split_top_level nor
    # _tokenize_column_definition recognized -- or /* */ comments, so an
    # ordinary inline comment inside a CREATE TABLE column list got scanned
    # as if it were column-definition source - fusing the real column that
    # follows into a bogus "--"-named column, dropping it from the schema
    # with no trace in `unsupported`.
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE things (
                id BIGINT PRIMARY KEY,
                -- deprecated
                name TEXT
            );
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert [c["name"] for c in result["tables"][0]["columns"]] == ["id", "name"]


def test_an_unbalanced_paren_inside_a_comment_does_not_swallow_the_next_table(tmp_path):
    # Batch 5 finding 2: _read_parenthesised_body's depth-tracking scan had
    # no comment awareness, so a stray "(" inside a comment (e.g. "see issue
    # (#123") bumped depth with nothing to close it until the *next* real
    # ")" in the file - which can belong to an entirely different, later
    # CREATE TABLE statement's own column definition, silently merging two
    # tables' worth of text into one and dropping the second table.
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE users (
                id BIGINT PRIMARY KEY,
                -- see issue (#123 for context, not closed here
                name TEXT NOT NULL
            );

            CREATE TABLE orders (
                id BIGINT PRIMARY KEY,
                user_id BIGINT REFERENCES users(id)
            );
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    table_names = [t["name"] for t in result["tables"]]
    assert "orders" in table_names
    assert [c["name"] for c in result["tables"][table_names.index("users")]["columns"]] == ["id", "name"]
    assert {
        "from_table": "orders", "from_column": "user_id", "to_table": "users",
        "to_column": "id", "on_delete": None, "name": "orders_user_id_fkey",
    } in [
        {k: v for k, v in r.items() if k != "file" and k != "line"} for r in result["relations"]
    ]


def test_a_semicolon_inside_a_block_comment_does_not_end_the_statement_early(tmp_path):
    # Batch 5 finding 3: _skip_to_statement_end special-cased -- comments
    # but had no branch for /* */ block comments, so a ; inside a block
    # comment was treated as the real statement terminator - splitting one
    # ALTER TABLE statement into garbage fragments and losing the real
    # column change.
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE widgets (id BIGINT PRIMARY KEY);
            ALTER TABLE widgets ADD COLUMN status TEXT /* values: active; inactive; deleted */ DEFAULT 'active';
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    columns = {c["name"]: c for c in result["tables"][0]["columns"]}
    assert "status" in columns
    assert columns["status"]["default"] == "'active'"
    assert result["unsupported"] == []


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


# ---------------------------------------------------------------------------
# sqlglot-parsed statement forms (composite constraints, DROP/ALTER/RENAME)
# ---------------------------------------------------------------------------


def test_composite_primary_key_and_table_level_unique(tmp_path):
    """The bug that motivated switching off the hand-written tokenizer: a
    table-level PRIMARY KEY/UNIQUE used to be silently dropped, with no
    unsupported entry to say why - every column came back non-primary-key."""
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE members (
                org_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                PRIMARY KEY (org_id, user_id),
                UNIQUE (user_id)
            );
            """
        },
    )
    result = extract_schema(repo, ["migrations"])
    columns = {c["name"]: c for c in result["tables"][0]["columns"]}

    assert columns["org_id"]["primary_key"] is True
    assert columns["org_id"]["nullable"] is False
    assert columns["user_id"]["primary_key"] is True
    assert columns["user_id"]["unique"] is True


def test_column_level_check_constraint_is_modeled(tmp_path):
    repo = write_migrations(
        tmp_path,
        {"001.sql": "CREATE TABLE t (id BIGINT PRIMARY KEY, role TEXT CHECK (role IN ('a', 'b')));"},
    )
    result = extract_schema(repo, ["migrations"])

    assert result["unsupported"] == []
    checks = result["tables"][0]["checks"]
    assert len(checks) == 1
    assert checks[0]["column"] == "role"
    assert checks[0]["name"] is None
    assert "role" in checks[0]["expression"]
    assert "'a'" in checks[0]["expression"]


def test_table_level_named_check_constraint_is_modeled(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (
                id BIGINT PRIMARY KEY,
                min_val INT,
                max_val INT,
                CONSTRAINT valid_range CHECK (min_val <= max_val)
            );
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert result["unsupported"] == []
    checks = result["tables"][0]["checks"]
    assert len(checks) == 1
    assert checks[0]["name"] == "valid_range"
    assert checks[0]["column"] is None


def test_check_constraint_added_via_alter_table_is_modeled(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id BIGINT PRIMARY KEY, age INT);
            ALTER TABLE t ADD CONSTRAINT age_check CHECK (age > 0);
            ALTER TABLE t ADD COLUMN role TEXT CHECK (role IN ('a', 'b'));
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert result["unsupported"] == []
    checks = result["tables"][0]["checks"]
    assert len(checks) == 2
    names_and_columns = {(c["name"], c["column"]) for c in checks}
    assert ("age_check", None) in names_and_columns
    assert (None, "role") in names_and_columns


def test_drop_table_removes_table_and_its_relations(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE parents (id BIGINT PRIMARY KEY);
            CREATE TABLE children (id BIGINT PRIMARY KEY, parent_id BIGINT REFERENCES parents(id));
            DROP TABLE children;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert [t["name"] for t in result["tables"]] == ["parents"]
    assert result["relations"] == []


def test_drop_column_rename_column_rename_table(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE widgets (id BIGINT PRIMARY KEY, old_name TEXT, junk TEXT);
            ALTER TABLE widgets DROP COLUMN junk;
            ALTER TABLE widgets RENAME COLUMN old_name TO label;
            ALTER TABLE widgets RENAME TO gadgets;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert [t["name"] for t in result["tables"]] == ["gadgets"]
    names = [c["name"] for c in result["tables"][0]["columns"]]
    assert names == ["id", "label"]


def test_rename_column_without_column_keyword(tmp_path):
    """Found via real-repo stress testing (coder/coder): Postgres allows
    omitting COLUMN in a rename (`RENAME old TO new`), and sqlglot parses
    that identically to a whole-table rename (`RENAME TO new`) at the
    action level - the real new column name only shows up in a separate
    ToTableProperty on the statement's own `options`. Without checking for
    it, `RENAME description TO readme` silently renamed the whole table to
    "description" - confirmed directly on a real migration, where it made
    the table invisible under its real name to every later migration that
    referenced it by name."""
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE template_versions (id BIGINT PRIMARY KEY, description TEXT);
            ALTER TABLE template_versions RENAME description TO readme;
            ALTER TABLE template_versions ADD COLUMN job_id BIGINT;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert [t["name"] for t in result["tables"]] == ["template_versions"]
    names = [c["name"] for c in result["tables"][0]["columns"]]
    assert names == ["id", "readme", "job_id"]
    assert not any("unknown table" in u["statement"] for u in result["unsupported"])


def test_alter_column_type_and_nullability(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id BIGINT PRIMARY KEY, note TEXT);
            ALTER TABLE t ALTER COLUMN note TYPE VARCHAR(200);
            ALTER TABLE t ALTER COLUMN note SET NOT NULL;
            """
        },
    )
    note = extract_schema(repo, ["migrations"])["tables"][0]["columns"][1]

    assert note["type"] == "VARCHAR(200)"
    assert note["nullable"] is False


def write_mysql_migrations(tmp_path: Path, files: dict[str, str]) -> Path:
    """Same as write_migrations, but also drops a schema.prisma declaring
    the mysql provider so extract_schema parses with the mysql dialect."""
    (tmp_path / "prisma").mkdir(exist_ok=True)
    (tmp_path / "prisma" / "schema.prisma").write_text(
        'datasource db {\n  provider = "mysql"\n  url = env("DATABASE_URL")\n}\n'
    )
    return write_migrations(tmp_path, files)


def test_mysql_change_column_renames_and_retypes(tmp_path):
    """Found via real-repo stress testing (vaultwarden's real MySQL
    migrations): CHANGE COLUMN (rename + retype in one clause, a real,
    common MySQL-specific ALTER TABLE form with no Postgres equivalent)
    fell to the generic unsupported catch-all entirely - a real gap in
    the multi-dialect support just added, not previously exercised
    because every prior corpus tested was Postgres."""
    repo = write_mysql_migrations(
        tmp_path,
        {
            "001.sql": (
                "CREATE TABLE t (id INT PRIMARY KEY, akey TEXT);"
                "ALTER TABLE t CHANGE COLUMN akey `key` TEXT NOT NULL;"
            )
        },
    )
    columns = extract_schema(repo, ["migrations"])["tables"][0]["columns"]

    assert [c["name"] for c in columns] == ["id", "key"]
    assert columns[1]["type"] == "TEXT"
    assert columns[1]["nullable"] is False


def test_mysql_modify_column_retypes_without_renaming(tmp_path):
    repo = write_mysql_migrations(
        tmp_path,
        {
            "001.sql": (
                "CREATE TABLE t (id INT PRIMARY KEY, note TEXT);"
                "ALTER TABLE t MODIFY COLUMN note VARCHAR(255) NOT NULL;"
            )
        },
    )
    columns = extract_schema(repo, ["migrations"])["tables"][0]["columns"]

    assert [c["name"] for c in columns] == ["id", "note"]
    assert columns[1]["type"] == "VARCHAR(255)"
    assert columns[1]["nullable"] is False


def test_mysql_modify_column_does_not_clear_existing_unique(tmp_path):
    """type/nullable are always applied (that's what CHANGE/MODIFY COLUMN
    is really about); unique/default/primary_key are only applied when
    the new definition actually states them, so a MODIFY COLUMN that's
    silent on UNIQUE doesn't clobber one a separate, earlier ADD
    CONSTRAINT UNIQUE already set."""
    repo = write_mysql_migrations(
        tmp_path,
        {
            "001.sql": (
                "CREATE TABLE t (id INT PRIMARY KEY, email TEXT);"
                "ALTER TABLE t ADD CONSTRAINT uq_email UNIQUE (email);"
                "ALTER TABLE t MODIFY COLUMN email VARCHAR(255) NOT NULL;"
            )
        },
    )
    email = extract_schema(repo, ["migrations"])["tables"][0]["columns"][1]

    assert email["type"] == "VARCHAR(255)"
    assert email["unique"] is True


def test_mysql_drop_primary_key_then_add_composite_primary_key(tmp_path):
    """Found via real-repo stress testing (vaultwarden): a real migration
    sequence drops a table's original single-column primary key, then
    adds a new composite one - both must apply in order for the final
    state to be correct."""
    repo = write_mysql_migrations(
        tmp_path,
        {
            "001.sql": (
                "CREATE TABLE t (uuid VARCHAR(36) PRIMARY KEY, user_uuid VARCHAR(36) NOT NULL);"
                "ALTER TABLE t DROP PRIMARY KEY;"
                "ALTER TABLE t ADD PRIMARY KEY (uuid, user_uuid);"
            )
        },
    )
    columns = extract_schema(repo, ["migrations"])["tables"][0]["columns"]
    by_name = {c["name"]: c for c in columns}

    assert by_name["uuid"]["primary_key"] is True
    assert by_name["user_uuid"]["primary_key"] is True


def test_add_constraint_unique_and_foreign_key(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE accounts (id BIGINT PRIMARY KEY);
            CREATE TABLE users (id BIGINT PRIMARY KEY, email TEXT, account_id BIGINT);
            ALTER TABLE users ADD CONSTRAINT uq_email UNIQUE (email);
            ALTER TABLE users ADD CONSTRAINT fk_account FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET NULL;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])
    users = next(t for t in result["tables"] if t["name"] == "users")
    email = next(c for c in users["columns"] if c["name"] == "email")

    assert email["unique"] is True
    relation = next(r for r in result["relations"] if r["from_column"] == "account_id")
    assert relation["to_table"] == "accounts"
    assert relation["on_delete"] == "SET NULL"


def test_named_table_level_primary_key_and_unique_constraints(tmp_path):
    """Found via real-repo stress testing (cal.com's Prisma-generated
    migrations, a common pg_dump/ORM convention): a *named*
    `CONSTRAINT x_pkey PRIMARY KEY (...)` wraps its PrimaryKey/
    UniqueColumnConstraint node inside a Constraint node - the unnamed
    (bare) form was already handled, the named form was not, so every
    Prisma-style migration's primary key came back unmarked."""
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE accounts (
                id BIGINT NOT NULL,
                email TEXT NOT NULL,
                CONSTRAINT accounts_pkey PRIMARY KEY (id),
                CONSTRAINT accounts_email_key UNIQUE (email)
            );
            """
        },
    )
    columns = {c["name"]: c for c in extract_schema(repo, ["migrations"])["tables"][0]["columns"]}

    assert columns["id"]["primary_key"] is True
    assert columns["id"]["nullable"] is False
    assert columns["email"]["unique"] is True


def test_named_table_level_foreign_key_constraint(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE parents (id BIGINT PRIMARY KEY);
            CREATE TABLE children (
                id BIGINT PRIMARY KEY,
                parent_id BIGINT NOT NULL,
                CONSTRAINT children_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES parents(id) ON DELETE CASCADE
            );
            """
        },
    )
    relations = extract_schema(repo, ["migrations"])["relations"]

    assert len(relations) == 1
    assert relations[0]["from_table"] == "children"
    assert relations[0]["to_table"] == "parents"
    assert relations[0]["on_delete"] == "CASCADE"


def test_alter_index_rename(tmp_path):
    """Found via real-repo stress testing: `ALTER INDEX ... RENAME TO ...`
    is a distinct statement kind (Alter with kind=INDEX), not an ALTER
    TABLE action - it fell straight through to the generic unsupported
    catch-all with no handling at all."""
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id BIGINT PRIMARY KEY, email TEXT);
            CREATE UNIQUE INDEX t_email_idx ON t (email);
            ALTER INDEX t_email_idx RENAME TO t_email_unique_idx;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert len(result["indexes"]) == 1
    assert result["indexes"][0]["name"] == "t_email_unique_idx"


def test_create_and_drop_index(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id BIGINT PRIMARY KEY, email TEXT);
            CREATE UNIQUE INDEX idx_t_email ON t (email);
            DROP INDEX idx_t_email;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert result["indexes"] == []


def test_truncated_create_index_is_unsupported_not_a_crash(tmp_path):
    """Found via real-repo stress testing on Discourse: a Rails migration
    builds its CREATE INDEX statement dynamically via string interpolation
    (`execute "CREATE INDEX #{...} name ON table (col)"`), and the raw-SQL
    extraction only captures the literal prefix `"CREATE INDEX "`. sqlglot
    parses the keywords but leaves the Index node itself as None, which used
    to crash `_sql_create_index_event` with an AttributeError instead of
    degrading to unsupported like every other unparseable statement."""
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": "CREATE INDEX ",
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert result["indexes"] == []
    assert any("CREATE INDEX" in entry["statement"] for entry in result["unsupported"])


def test_drop_constraint_by_name_removes_the_matching_relation(tmp_path):
    """A named FK constraint's name is tracked (explicit, or Postgres' own
    auto-generated <table>_<column>_fkey for an unnamed one), so a later
    DROP CONSTRAINT by that name can actually be resolved instead of
    always falling to unsupported."""
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id BIGINT PRIMARY KEY);
            ALTER TABLE t ADD CONSTRAINT fk_x FOREIGN KEY (id) REFERENCES t(id);
            ALTER TABLE t DROP CONSTRAINT fk_x;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert result["relations"] == []
    assert not any("DROP CONSTRAINT" in u["statement"] for u in result["unsupported"])


def test_drop_constraint_with_no_matching_relation_stays_unsupported(tmp_path):
    """A DROP CONSTRAINT targeting a name that isn't a tracked relation -
    most likely a named UNIQUE/CHECK/PRIMARY KEY constraint - still falls
    back to unsupported (with the real statement text) rather than
    silently doing nothing."""
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id BIGINT PRIMARY KEY, email TEXT);
            ALTER TABLE t ADD CONSTRAINT uq_email UNIQUE (email);
            ALTER TABLE t DROP CONSTRAINT uq_email;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert any("DROP CONSTRAINT uq_email" in u["statement"] for u in result["unsupported"])


def test_views_and_grants_are_unsupported(tmp_path):
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id BIGINT PRIMARY KEY);
            CREATE VIEW v AS SELECT * FROM t;
            GRANT SELECT ON t TO readonly;
            """
        },
    )
    result = extract_schema(repo, ["migrations"])
    statements = [u["statement"] for u in result["unsupported"]]

    assert any("CREATE VIEW" in s for s in statements)
    assert any("GRANT" in s for s in statements)


def test_malformed_sql_does_not_raise(tmp_path):
    repo = write_migrations(
        tmp_path,
        {"001.sql": "CREATE TABLE good (id BIGINT PRIMARY KEY); THIS IS $$ NOT VALID ! ! !"},
    )
    result = extract_schema(repo, ["migrations"])

    assert [t["name"] for t in result["tables"]] == ["good"]


# ---------------------------------------------------------------------------
# Dialect detection - explicit config signals only, never guessed from SQL
# ---------------------------------------------------------------------------


def test_no_config_file_defaults_to_postgres(tmp_path):
    repo = write_migrations(tmp_path, {"001.sql": "CREATE TABLE t (id BIGINT PRIMARY KEY);"})
    result = extract_schema(repo, ["migrations"])
    assert result["dialect"] == ["postgresql"]


def test_prisma_schema_mysql_detected_and_parsed_correctly(tmp_path):
    (tmp_path / "prisma").mkdir()
    (tmp_path / "prisma" / "schema.prisma").write_text(
        'datasource db {\n  provider = "mysql"\n  url = env("DATABASE_URL")\n}\n'
    )
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": (
                "CREATE TABLE `users` (`id` INT NOT NULL AUTO_INCREMENT, "
                "`email` VARCHAR(255) NOT NULL, PRIMARY KEY (`id`)) ENGINE=InnoDB;"
            )
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert result["dialect"] == ["mysql"]
    assert [c["name"] for c in result["tables"][0]["columns"]] == ["id", "email"]


def test_fk_auto_naming_is_postgres_only(tmp_path):
    """<table>_<column>_fkey is a real Postgres default-naming convention,
    not a general SQL one - MySQL auto-names FKs `<table>_ibfk_<n>`
    (sequential, not derivable from the column name at all) and SQLite
    doesn't assign one the same way. Applying the Postgres convention
    under a different active dialect would fabricate a wrong name, which
    would then make DROP CONSTRAINT resolution match on a name Postgres
    itself would never actually produce."""
    (tmp_path / "prisma").mkdir()
    (tmp_path / "prisma" / "schema.prisma").write_text(
        'datasource db {\n  provider = "mysql"\n  url = env("DATABASE_URL")\n}\n'
    )
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": (
                "CREATE TABLE `accounts` (`id` INT NOT NULL, PRIMARY KEY (`id`));"
                "CREATE TABLE `users` (`id` INT NOT NULL, `account_id` INT, "
                "PRIMARY KEY (`id`), FOREIGN KEY (`account_id`) REFERENCES `accounts` (`id`));"
            )
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert result["dialect"] == ["mysql"]
    assert len(result["relations"]) == 1
    assert result["relations"][0]["name"] is None
    assert result["tables"][0]["columns"][0]["primary_key"] is True


def test_dialect_detected_per_migration_directory(tmp_path):
    """Found via real-repo stress testing (vaultwarden, memos): a real,
    common convention for a project shipping migrations for more than one
    backend is a dialect-named subdirectory per backend (migrations/mysql/,
    migrations/postgresql/, migrations/sqlite/), each with real
    dialect-specific SQL. Before this, the whole call used one
    repo-wide-detected dialect, which would mis-parse two thirds of a
    three-backend repo like this. A migration file's own path is now
    checked per file and wins over the repo-wide config signal."""
    (tmp_path / "mysql").mkdir()
    (tmp_path / "mysql" / "001.sql").write_text(
        "CREATE TABLE `t` (`id` INT NOT NULL AUTO_INCREMENT, PRIMARY KEY (`id`));"
    )
    (tmp_path / "sqlite").mkdir()
    (tmp_path / "sqlite" / "001.sql").write_text(
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY AUTOINCREMENT);"
    )
    result = extract_schema(tmp_path, ["mysql", "sqlite"])

    assert result["dialect"] == ["mysql", "sqlite"]
    assert {t["name"] for t in result["tables"]} == {"t", "t2"}
    for table in result["tables"]:
        assert table["columns"][0]["primary_key"] is True


def test_rails_database_yml_sqlite_detected_and_parsed_correctly(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "database.yml").write_text(
        "production:\n  adapter: sqlite3\n  database: db/production.sqlite3\n"
    )
    repo = write_migrations(
        tmp_path,
        {"001.sql": "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL);"},
    )
    result = extract_schema(repo, ["migrations"])

    assert result["dialect"] == ["sqlite"]
    assert [c["name"] for c in result["tables"][0]["columns"]] == ["id", "email"]


def test_alembic_ini_mysql_url_detected(tmp_path):
    (tmp_path / "alembic.ini").write_text(
        "[alembic]\nsqlalchemy.url = mysql+pymysql://user:pass@localhost/dbname\n"
    )
    repo = write_migrations(tmp_path, {"001.sql": "CREATE TABLE t (id BIGINT PRIMARY KEY);"})
    result = extract_schema(repo, ["migrations"])
    assert result["dialect"] == ["mysql"]


def test_django_settings_postgres_engine_detected(tmp_path):
    (tmp_path / "settings.py").write_text(
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql'}}\n"
    )
    repo = write_migrations(tmp_path, {"001.sql": "CREATE TABLE t (id BIGINT PRIMARY KEY);"})
    result = extract_schema(repo, ["migrations"])
    assert result["dialect"] == ["postgresql"]


def test_knexfile_dialect_detected(tmp_path):
    (tmp_path / "knexfile.js").write_text("module.exports = { client: 'sqlite3' };\n")
    repo = write_migrations(tmp_path, {"001.sql": "CREATE TABLE t (id INTEGER PRIMARY KEY);"})
    result = extract_schema(repo, ["migrations"])
    assert result["dialect"] == ["sqlite"]


def test_sqlite_trigger_with_internal_semicolons_is_not_fragmented(tmp_path):
    """Found via real-repo stress testing (usememos/memos' real SQLite
    trigger migrations): a bare BEGIN...END trigger body (no dollar-
    quoting, unlike Postgres functions) has its own internal semicolons,
    which a naive split treated as real statement boundaries - breaking
    one CREATE TRIGGER into a truncated fragment plus a stray bare "END"
    entry. Both must now come back as a single clean unsupported entry
    (CREATE TRIGGER is correctly out of scope either way, but the
    reporting itself must not be corrupted), and a real statement
    following the trigger in the same file must still parse correctly."""
    (tmp_path / "knexfile.js").write_text("module.exports = { client: 'sqlite3' };\n")
    repo = write_migrations(
        tmp_path,
        {
            "001.sql": """
            CREATE TABLE t (id INTEGER PRIMARY KEY, updated_ts INTEGER);
            CREATE TRIGGER trig AFTER UPDATE ON t BEGIN
            UPDATE t SET updated_ts = 1 WHERE rowid = old.rowid;
            END;
            CREATE TABLE t2 (id INTEGER PRIMARY KEY);
            """
        },
    )
    result = extract_schema(repo, ["migrations"])

    assert [t["name"] for t in result["tables"]] == ["t", "t2"]
    trigger_entries = [u for u in result["unsupported"] if u["statement"].startswith("CREATE TRIGGER")]
    assert len(trigger_entries) == 1
    assert not any(u["statement"].strip() == "END" for u in result["unsupported"])
