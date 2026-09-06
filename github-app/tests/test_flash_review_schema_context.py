from scan_worker.flash_review_schema_context import (
    build_schema_endpoint_context,
    _is_migration_file,
    _sql_dialect_for,
    _summarize_event,
)


def _evidence(migration_dirs=None, dialect=None, endpoints=None):
    return {
        "repository": {
            "database": {
                "migration_directories": [{"path": p} for p in (migration_dirs or [])],
                "schema": {"dialect": dialect or []},
            },
            "api_endpoints": {"endpoints": endpoints or []},
        }
    }


def test_returns_empty_string_when_no_evidence():
    assert build_schema_endpoint_context(None, ["a.rb"], {}) == ""


def test_returns_empty_string_when_no_changed_file_matches_anything():
    evidence = _evidence(migration_dirs=["db/migrate"])
    assert build_schema_endpoint_context(evidence, ["app/models/user.rb"], {}) == ""


def test_rails_migration_drop_and_add_column_produce_real_citable_facts():
    evidence = _evidence(migration_dirs=["db/migrate"])
    content = """
class RemoveLegacyIdFromUsers < ActiveRecord::Migration[7.0]
  def change
    remove_column :users, :legacy_id
    add_column :users, :deleted_at, :datetime
  end
end
"""
    context = build_schema_endpoint_context(
        evidence, ["db/migrate/002_remove_legacy_id.rb"],
        {"db/migrate/002_remove_legacy_id.rb": content},
    )
    assert "db/migrate/002_remove_legacy_id.rb is a migration" in context
    assert "DROP COLUMN users.legacy_id" in context
    assert "ADD COLUMN users.deleted_at (DATETIME)" in context


def test_django_migration_produces_real_facts():
    evidence = _evidence(migration_dirs=["blog/migrations"])
    content = """
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.RemoveField(model_name='post', name='legacy_id'),
    ]
"""
    context = build_schema_endpoint_context(
        evidence, ["blog/migrations/0002_remove_legacy_id.py"],
        {"blog/migrations/0002_remove_legacy_id.py": content},
    )
    assert "DROP COLUMN blog_post.legacy_id" in context


def test_alembic_migration_produces_real_facts():
    evidence = _evidence(migration_dirs=["alembic/versions"])
    content = """
revision = "abc123"
down_revision = "prior456"

def upgrade():
    op.drop_column('users', 'legacy_id')

def downgrade():
    pass
"""
    context = build_schema_endpoint_context(
        evidence, ["alembic/versions/abc123_remove_legacy_id.py"],
        {"alembic/versions/abc123_remove_legacy_id.py": content},
    )
    assert "DROP COLUMN users.legacy_id" in context


def test_raw_sql_migration_uses_dialect_from_evidence():
    evidence = _evidence(migration_dirs=["migrations"], dialect=["postgresql"])
    content = "ALTER TABLE users DROP COLUMN legacy_id;"
    context = build_schema_endpoint_context(
        evidence, ["migrations/003_drop_legacy_id.sql"],
        {"migrations/003_drop_legacy_id.sql": content},
    )
    assert "DROP COLUMN users.legacy_id" in context


def test_migration_file_with_no_fetched_content_is_skipped_not_crashed():
    evidence = _evidence(migration_dirs=["db/migrate"])
    context = build_schema_endpoint_context(
        evidence, ["db/migrate/002_remove_legacy_id.rb"], {},
    )
    assert context == ""


def test_non_migration_file_in_migration_looking_extension_is_ignored():
    # A .rb file that isn't under a known migration directory must not be
    # parsed as a migration - app code can contain calls that happen to
    # look like Rails migration DSL methods.
    evidence = _evidence(migration_dirs=["db/migrate"])
    context = build_schema_endpoint_context(
        evidence, ["app/models/user.rb"],
        {"app/models/user.rb": "remove_column :users, :legacy_id"},
    )
    assert context == ""


def test_endpoint_file_lists_its_real_resolved_endpoints():
    evidence = _evidence(endpoints=[
        {"method": "GET", "path": "/users/{id}", "file": "app/routes.rb", "line": 12,
         "handler": "show", "unresolved": False},
        {"method": "DELETE", "path": "/users/{id}", "file": "app/routes.rb", "line": 20,
         "handler": "destroy", "unresolved": False},
    ])
    context = build_schema_endpoint_context(evidence, ["app/routes.rb"], {})
    assert "app/routes.rb currently defines these API endpoints:" in context
    assert "GET /users/{id} -> show (app/routes.rb:12)" in context
    assert "DELETE /users/{id} -> destroy (app/routes.rb:20)" in context


def test_unresolved_endpoints_are_excluded():
    evidence = _evidence(endpoints=[
        {"method": "GET", "path": None, "file": "app/routes.rb", "line": 5,
         "handler": "dynamic", "unresolved": True},
    ])
    context = build_schema_endpoint_context(evidence, ["app/routes.rb"], {})
    assert context == ""


def test_both_migration_and_endpoint_facts_can_appear_together():
    evidence = _evidence(
        migration_dirs=["db/migrate"],
        endpoints=[{"method": "GET", "path": "/x", "file": "app/routes.rb", "line": 1,
                    "handler": "h", "unresolved": False}],
    )
    content = "add_column :users, :deleted_at, :datetime"
    context = build_schema_endpoint_context(
        evidence, ["db/migrate/001.rb", "app/routes.rb"],
        {"db/migrate/001.rb": content},
    )
    assert "ADD COLUMN users.deleted_at" in context
    assert "GET /x -> h" in context


def test_byte_budget_truncates_rather_than_failing():
    import scan_worker.flash_review_schema_context as mod
    original = mod.MAX_SCHEMA_ENDPOINT_BYTES
    mod.MAX_SCHEMA_ENDPOINT_BYTES = 10
    try:
        evidence = _evidence(migration_dirs=["db/migrate"])
        content = "add_column :users, :deleted_at, :datetime"
        context = build_schema_endpoint_context(
            evidence, ["db/migrate/001.rb"], {"db/migrate/001.rb": content},
        )
        assert len(context.encode("utf-8")) <= 10 + len(
            "--- deterministic schema/endpoint facts for changed files (not conclusions) ---\n"
        )
    finally:
        mod.MAX_SCHEMA_ENDPOINT_BYTES = original


def test_is_migration_file_matches_exact_and_nested_paths():
    evidence = _evidence(migration_dirs=["db/migrate"])
    assert _is_migration_file(evidence, "db/migrate/001.rb") is True
    assert _is_migration_file(evidence, "db/migrate/nested/001.rb") is True
    assert _is_migration_file(evidence, "app/models/user.rb") is False
    assert _is_migration_file(evidence, "db/migrated_notes.txt") is False  # prefix, not a real subpath


def test_sql_dialect_for_maps_display_form_and_defaults_to_postgres():
    assert _sql_dialect_for(_evidence(dialect=["postgresql"])) == "postgres"
    assert _sql_dialect_for(_evidence(dialect=["mysql"])) == "mysql"
    assert _sql_dialect_for(_evidence(dialect=["rails"])) == "postgres"  # non-SQL dialect tag, falls back
    assert _sql_dialect_for(_evidence(dialect=[])) == "postgres"


def test_summarize_event_covers_the_less_obvious_kinds():
    assert _summarize_event({"kind": "rename_column", "table": "users",
                              "old_name": "legacy_id", "new_name": "id"}) == \
        "RENAME COLUMN users.legacy_id -> id"
    assert _summarize_event({"kind": "remove_table", "table": "widgets"}) == "DROP TABLE widgets"
    assert _summarize_event({"kind": "rename_table", "old_name": "widgets", "new_name": "gadgets"}) == \
        "RENAME TABLE widgets -> gadgets"
    assert _summarize_event({
        "kind": "add_relation", "table": "posts",
        "relation": {"from_column": "author_id", "to_table": "users", "to_column": "id"},
    }) == "ADD FOREIGN KEY posts.author_id -> users.id"
    assert _summarize_event({"kind": "remove_relation", "table": "posts", "name": "posts_author_id_fkey"}) == \
        "DROP CONSTRAINT posts_author_id_fkey on posts"
    assert _summarize_event({"kind": "create_index", "table": "users", "name": "idx_email",
                              "columns": ["email"]}) == "CREATE INDEX idx_email ON users (email)"
    assert _summarize_event({"kind": "remove_index", "name": "idx_email"}) == "DROP INDEX idx_email"
    assert _summarize_event({"kind": "drop_primary_key", "table": "widgets"}) == "DROP PRIMARY KEY on widgets"
    assert _summarize_event({"kind": "alter_column", "table": "users", "name": "email",
                              "changes": {"nullable": False}}) == "ALTER COLUMN users.email (nullable=False)"
    assert _summarize_event({"kind": "alter_column", "table": "users", "name": "email",
                              "changes": {"nullable": None}}) is None
    assert _summarize_event({"kind": "unsupported", "statement": "x"}) is None
    assert _summarize_event({"kind": "raw_sql", "sql": "x"}) is None
