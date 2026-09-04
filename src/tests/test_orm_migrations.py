from pathlib import Path

from aletheore.orm_migrations import (
    extract_alembic_migrations,
    extract_django_migrations,
    extract_rails_migrations,
)
from aletheore.schema_map import extract_schema


def write_files(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel_path, body in files.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return tmp_path


# ---------------------------------------------------------------------------
# Django
# ---------------------------------------------------------------------------


def test_django_create_model_infers_table_name_and_implicit_pk(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "blog/migrations/0001_initial.py": """
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name='Post',
            fields=[
                ('title', models.CharField(max_length=200)),
                ('published', models.BooleanField(default=False)),
            ],
        ),
    ]
"""
        },
    )
    events, sources = extract_django_migrations(repo, ["blog/migrations"])
    assert sources == ["blog/migrations/0001_initial.py"]
    create = next(e for e in events if e["kind"] == "create_table")
    assert create["table"] == "blog_post"
    names = [c["name"] for c in create["columns"]]
    # No field in the migration is marked primary_key=True, so Django's
    # implicit `id` column must be injected first.
    assert names == ["id", "title", "published"]
    assert create["columns"][0]["primary_key"] is True
    assert create["columns"][2]["default"] == "False"


def test_django_foreign_key_resolves_to_real_table_name(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "blog/migrations/0001_initial.py": """
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name='Post',
            fields=[
                ('id', models.AutoField(primary_key=True)),
                ('author', models.ForeignKey(to='accounts.User', on_delete=models.CASCADE)),
            ],
        ),
    ]
"""
        },
    )
    events, _ = extract_django_migrations(repo, ["blog/migrations"])
    create = next(e for e in events if e["kind"] == "create_table")
    assert len(create["relations"]) == 1
    relation = create["relations"][0]
    assert relation["from_column"] == "author_id"
    assert relation["to_table"] == "accounts_user"
    assert relation["to_column"] == "id"
    assert relation["on_delete"] == "CASCADE"
    assert relation["file"] == "blog/migrations/0001_initial.py"
    assert any(c["name"] == "author_id" for c in create["columns"])


def test_django_flexible_foreign_key_subclass_is_a_real_relation(tmp_path):
    """Found via real-repo stress testing on Sentry: 249 real FK fields in
    a single squashed migration use Sentry's own
    sentry.db.models.fields.foreignkey.FlexibleForeignKey - a thin,
    verified `django.db.models.ForeignKey` subclass (only defaults
    on_delete) - which the field-type check missed entirely since it only
    recognized the literal names ForeignKey/OneToOneField."""
    repo = write_files(
        tmp_path,
        {
            "blog/migrations/0001_initial.py": """
from django.db import migrations, models
import sentry.db.models.fields.foreignkey

class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name='Post',
            fields=[
                ('id', models.AutoField(primary_key=True)),
                ('owner', sentry.db.models.fields.foreignkey.FlexibleForeignKey(
                    to='accounts.User', on_delete=models.CASCADE,
                )),
            ],
        ),
    ]
"""
        },
    )
    events, _ = extract_django_migrations(repo, ["blog/migrations"])
    create = next(e for e in events if e["kind"] == "create_table")
    assert len(create["relations"]) == 1
    relation = create["relations"][0]
    assert relation["from_column"] == "owner_id"
    assert relation["to_table"] == "accounts_user"


def test_django_unresolvable_foreign_key_target_is_unsupported_not_a_broken_relation(tmp_path):
    """Found via real-repo stress testing on Sentry: `to=settings.AUTH_USER_MODEL`
    is a real, common idiom - not a static string literal, so the target
    table can't be resolved. This used to still emit a "relation" with
    to_table=None instead of recording the gap as unsupported."""
    repo = write_files(
        tmp_path,
        {
            "blog/migrations/0001_initial.py": """
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name='Post',
            fields=[
                ('id', models.AutoField(primary_key=True)),
                ('owner', models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE)),
            ],
        ),
    ]
"""
        },
    )
    events, _ = extract_django_migrations(repo, ["blog/migrations"])
    create = next(e for e in events if e["kind"] == "create_table")
    assert create["relations"] == []
    assert any(c["name"] == "owner_id" for c in create["columns"])
    unsupported = [e for e in events if e["kind"] == "unsupported"]
    assert len(unsupported) == 1
    assert "owner_id" in unsupported[0]["statement"]
    assert "settings.AUTH_USER_MODEL" in unsupported[0]["statement"]


def test_django_self_referential_foreign_key(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "org/migrations/0001_initial.py": """
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name='Employee',
            fields=[
                ('id', models.AutoField(primary_key=True)),
                ('manager', models.ForeignKey(to='self', on_delete=models.SET_NULL, null=True)),
            ],
        ),
    ]
"""
        },
    )
    events, _ = extract_django_migrations(repo, ["org/migrations"])
    create = next(e for e in events if e["kind"] == "create_table")
    assert create["relations"][0]["to_table"] == "org_employee"
    assert create["relations"][0]["on_delete"] == "SET_NULL"


def test_django_add_field_and_add_index(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "blog/migrations/0001_initial.py": """
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name='Post',
            fields=[('id', models.AutoField(primary_key=True))],
        ),
    ]
""",
            "blog/migrations/0002_add_bits.py": """
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.AddField(model_name='post', name='slug', field=models.SlugField(unique=True)),
        migrations.AddIndex(model_name='post', index=models.Index(fields=['slug'], name='post_slug_idx')),
    ]
""",
        },
    )
    result = extract_schema(repo, ["blog/migrations"])
    table = next(t for t in result["tables"] if t["name"] == "blog_post")
    assert any(c["name"] == "slug" and c["unique"] for c in table["columns"])
    assert len(result["indexes"]) == 1
    index = result["indexes"][0]
    assert index["name"] == "post_slug_idx"
    assert index["table"] == "blog_post"
    assert index["columns"] == ["slug"]
    assert index["unique"] is False
    assert index["file"] == "blog/migrations/0002_add_bits.py"
    assert result["dialect"] == ["django"]


def test_django_remove_field_alter_field_rename_field_delete_model(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "blog/migrations/0001_initial.py": """
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(
            name='Post',
            fields=[
                ('id', models.AutoField(primary_key=True)),
                ('title', models.CharField(max_length=100)),
                ('old_field', models.TextField()),
            ],
        ),
        migrations.CreateModel(
            name='Draft',
            fields=[('id', models.AutoField(primary_key=True))],
        ),
    ]
""",
            "blog/migrations/0002_alter_bits.py": """
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.RemoveField(model_name='post', name='old_field'),
        migrations.AlterField(model_name='post', name='title', field=models.CharField(max_length=300, unique=True)),
        migrations.RenameField(model_name='post', old_name='title', new_name='headline'),
        migrations.DeleteModel(name='Draft'),
    ]
""",
        },
    )
    result = extract_schema(repo, ["blog/migrations"])
    table = next(t for t in result["tables"] if t["name"] == "blog_post")
    names = [c["name"] for c in table["columns"]]
    assert "old_field" not in names
    assert "title" not in names
    headline = next(c for c in table["columns"] if c["name"] == "headline")
    assert headline["type"] == "CHARFIELD"
    assert headline["unique"] is True
    assert not any(t["name"] == "blog_draft" for t in result["tables"])
    assert result["unsupported"] == []


def test_django_run_sql_replays_through_sql_parser(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "blog/migrations/0001_initial.py": """
from django.db import migrations

class Migration(migrations.Migration):
    operations = [
        migrations.RunSQL(sql="CREATE TABLE legacy (id BIGINT PRIMARY KEY, note TEXT);"),
    ]
"""
        },
    )
    result = extract_schema(repo, ["blog/migrations"])
    assert [t["name"] for t in result["tables"]] == ["legacy"]
    assert [c["name"] for c in result["tables"][0]["columns"]] == ["id", "note"]


def test_django_run_python_and_alter_model_options_stay_unsupported(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "blog/migrations/0001_initial.py": """
from django.db import migrations

def seed_data(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    operations = [
        migrations.RunPython(seed_data),
        migrations.AlterModelOptions(name='post', options={'ordering': ['-id']}),
    ]
"""
        },
    )
    events, _ = extract_django_migrations(repo, ["blog/migrations"])
    assert len(events) == 2
    assert all(e["kind"] == "unsupported" for e in events)
    assert "RunPython" in events[0]["statement"]
    assert "AlterModelOptions" in events[1]["statement"]


def test_non_django_migrations_directory_is_ignored(tmp_path):
    """A `migrations/` directory that isn't Django (no django import, no
    Migration class) must not be mis-parsed."""
    repo = write_files(
        tmp_path,
        {"tool/migrations/0001_something.py": "print('not django at all')\n"},
    )
    events, sources = extract_django_migrations(repo, ["tool/migrations"])
    assert events == []
    assert sources == []


# ---------------------------------------------------------------------------
# Rails
# ---------------------------------------------------------------------------


def test_rails_create_table_with_references_and_timestamps(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "db/migrate/20230101000000_create_posts.rb": """
class CreatePosts < ActiveRecord::Migration[7.0]
  def change
    create_table :posts do |t|
      t.string :title, null: false
      t.text :body
      t.references :author, foreign_key: true
      t.timestamps
    end
  end
end
"""
        },
    )
    events, sources = extract_rails_migrations(repo, ["db/migrate"])
    assert sources == ["db/migrate/20230101000000_create_posts.rb"]
    create = next(e for e in events if e["kind"] == "create_table")
    assert create["table"] == "posts"
    names = [c["name"] for c in create["columns"]]
    assert names == ["id", "title", "body", "author_id", "created_at", "updated_at"]
    assert create["columns"][0]["primary_key"] is True
    assert create["columns"][1]["nullable"] is False
    assert create["columns"][2]["nullable"] is True
    assert len(create["relations"]) == 1
    relation = create["relations"][0]
    assert relation["from_column"] == "author_id"
    assert relation["to_table"] == "authors"
    assert relation["to_column"] == "id"
    assert relation["on_delete"] is None
    assert relation["file"] == "db/migrate/20230101000000_create_posts.rb"


def test_rails_id_false_omits_primary_key(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "db/migrate/20230101000000_create_join.rb": """
class CreateJoin < ActiveRecord::Migration[7.0]
  def change
    create_table :posts_tags, id: false do |t|
      t.integer :post_id
      t.integer :tag_id
    end
  end
end
"""
        },
    )
    events, _ = extract_rails_migrations(repo, ["db/migrate"])
    create = next(e for e in events if e["kind"] == "create_table")
    assert [c["name"] for c in create["columns"]] == ["post_id", "tag_id"]


def test_rails_standalone_add_column_add_index_add_foreign_key(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "db/migrate/20230101000000_create_posts.rb": """
class CreatePosts < ActiveRecord::Migration[7.0]
  def change
    create_table :posts do |t|
      t.string :title
      t.string :old
    end
  end
end
""",
            "db/migrate/20230102000000_alter_posts.rb": """
class AlterPosts < ActiveRecord::Migration[7.0]
  def change
    add_column :posts, :views, :integer, null: false, default: 0
    add_index :posts, [:title], unique: true, name: "idx_posts_title"
    add_foreign_key :posts, :accounts, column: :account_id, on_delete: :cascade
    remove_column :posts, :old
  end
end
""",
        },
    )
    result = extract_schema(repo, ["db/migrate"])
    table = next(t for t in result["tables"] if t["name"] == "posts")
    views = next(c for c in table["columns"] if c["name"] == "views")
    assert views["nullable"] is False
    assert views["default"] == "0"
    assert not any(c["name"] == "old" for c in table["columns"])
    assert len(result["indexes"]) == 1
    index = result["indexes"][0]
    assert index["name"] == "idx_posts_title"
    assert index["table"] == "posts"
    assert index["columns"] == ["title"]
    assert index["unique"] is True
    assert index["file"] == "db/migrate/20230102000000_alter_posts.rb"
    fk = next(r for r in result["relations"] if r["from_column"] == "account_id")
    assert fk["to_table"] == "accounts"
    assert fk["on_delete"] == "CASCADE"
    assert result["unsupported"] == []


def test_rails_add_index_with_constant_name_falls_back_to_auto_name(tmp_path):
    """Found via real-repo stress testing on Discourse: `add_index` with a
    `name:` kwarg that references a constant (not a static string/symbol
    literal) can't be resolved by `_rb_symbol_text`, so `index_name` was left
    None instead of falling back to Rails' own auto-generated name - which
    then crashed `extract_schema`'s final `indexes.sort()` on a NoneType
    comparison instead of degrading gracefully."""
    repo = write_files(
        tmp_path,
        {
            "db/migrate/20230101000000_create_events.rb": """
class CreateEvents < ActiveRecord::Migration[7.0]
  def change
    create_table :events do |t|
      t.string :kind
    end
  end
end
""",
            "db/migrate/20230102000000_add_kind_index.rb": """
class AddKindIndex < ActiveRecord::Migration[7.0]
  INDEX_NAME = "idx_events_kind"

  def up
    add_index :events, :kind, name: INDEX_NAME
  end
end
""",
        },
    )
    result = extract_schema(repo, ["db/migrate"])
    assert len(result["indexes"]) == 1
    index = result["indexes"][0]
    assert index["table"] == "events"
    assert index["columns"] == ["kind"]
    assert index["name"] == "index_events_on_kind"


def test_rails_rename_column_rename_table_drop_table(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "db/migrate/20230101000000_create_things.rb": """
class CreateThings < ActiveRecord::Migration[7.0]
  def change
    create_table :widgets do |t|
      t.string :name
    end
    create_table :scratch do |t|
      t.string :x
    end
  end
end
""",
            "db/migrate/20230102000000_alter_things.rb": """
class AlterThings < ActiveRecord::Migration[7.0]
  def change
    rename_column :widgets, :name, :label
    rename_table :widgets, :gadgets
    drop_table :scratch
  end
end
""",
        },
    )
    result = extract_schema(repo, ["db/migrate"])
    table_names = [t["name"] for t in result["tables"]]
    assert "gadgets" in table_names
    assert "widgets" not in table_names
    assert "scratch" not in table_names
    gadgets = next(t for t in result["tables"] if t["name"] == "gadgets")
    assert [c["name"] for c in gadgets["columns"]] == ["id", "label"]


def test_rails_change_table_block(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "db/migrate/20230101000000_create_posts.rb": """
class CreatePosts < ActiveRecord::Migration[7.0]
  def change
    create_table :posts do |t|
      t.string :title
      t.string :subtitle
    end
  end
end
""",
            "db/migrate/20230102000000_change_posts.rb": """
class ChangePosts < ActiveRecord::Migration[7.0]
  def change
    change_table :posts do |t|
      t.remove :subtitle
      t.rename :title, :headline
      t.integer :views
      t.timestamps
    end
  end
end
""",
        },
    )
    result = extract_schema(repo, ["db/migrate"])
    table = next(t for t in result["tables"] if t["name"] == "posts")
    names = [c["name"] for c in table["columns"]]
    assert "subtitle" not in names
    assert "title" not in names
    assert set(["headline", "views", "created_at", "updated_at"]) <= set(names)


def test_rails_change_column_and_null_and_default(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "db/migrate/20230101000000_create_posts.rb": """
class CreatePosts < ActiveRecord::Migration[7.0]
  def change
    create_table :posts do |t|
      t.string :views
    end
  end
end
""",
            "db/migrate/20230102000000_alter_posts.rb": """
class AlterPosts < ActiveRecord::Migration[7.0]
  def change
    change_column :posts, :views, :integer
    change_column_null :posts, :views, false
    change_column_default :posts, :views, 0
  end
end
""",
        },
    )
    result = extract_schema(repo, ["db/migrate"])
    table = next(t for t in result["tables"] if t["name"] == "posts")
    views = next(c for c in table["columns"] if c["name"] == "views")
    assert views["type"] == "INTEGER"
    assert views["nullable"] is False
    assert views["default"] == "0"


def test_rails_execute_replays_through_sql_parser(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "db/migrate/20230101000000_raw.rb": """
class Raw < ActiveRecord::Migration[7.0]
  def change
    execute "CREATE TABLE legacy (id BIGINT PRIMARY KEY, note TEXT);"
  end
end
"""
        },
    )
    result = extract_schema(repo, ["db/migrate"])
    assert [t["name"] for t in result["tables"]] == ["legacy"]


def test_rails_create_join_table_and_remove_foreign_key_stay_unsupported(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "db/migrate/20230101000000_misc.rb": """
class Misc < ActiveRecord::Migration[7.0]
  def change
    create_join_table :posts, :tags
    remove_foreign_key :posts, :accounts
  end
end
"""
        },
    )
    events, _ = extract_rails_migrations(repo, ["db/migrate"])
    assert len(events) == 2
    assert all(e["kind"] == "unsupported" for e in events)


# ---------------------------------------------------------------------------
# Alembic
# ---------------------------------------------------------------------------


def test_alembic_create_table_only_reads_upgrade(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "alembic/versions/abc123_init.py": """
from alembic import op
import sqlalchemy as sa

revision = "abc123"
down_revision = None

def upgrade():
    op.create_table('accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False),
    )

def downgrade():
    op.drop_table('accounts')
"""
        },
    )
    events, sources = extract_alembic_migrations(repo, ["alembic/versions"])
    assert sources == ["alembic/versions/abc123_init.py"]
    kinds = [e["kind"] for e in events]
    assert kinds == ["create_table"]
    create = events[0]
    assert create["table"] == "accounts"
    assert [c["name"] for c in create["columns"]] == ["id", "name"]
    assert create["columns"][0]["primary_key"] is True
    assert create["columns"][1]["type"] == "STRING"


def test_alembic_inline_foreign_key_and_add_column_and_index(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "alembic/versions/abc123_init.py": """
from alembic import op
import sqlalchemy as sa

revision = "abc123"
down_revision = None

def upgrade():
    op.create_table('accounts', sa.Column('id', sa.Integer(), primary_key=True))
""",
            "alembic/versions/def456_add_posts.py": """
from alembic import op
import sqlalchemy as sa

revision = "abc123"
down_revision = None

def upgrade():
    op.create_table('posts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False),
    )
    op.add_column('posts', sa.Column('title', sa.String(length=200), nullable=True))
    op.create_index('ix_posts_title', 'posts', ['title'], unique=False)
"""
        },
    )
    result = extract_schema(repo, ["alembic/versions"])
    posts = next(t for t in result["tables"] if t["name"] == "posts")
    assert [c["name"] for c in posts["columns"]] == ["id", "account_id", "title"]
    fk = next(r for r in result["relations"] if r["from_column"] == "account_id")
    assert fk["to_table"] == "accounts"
    assert fk["to_column"] == "id"
    assert result["indexes"][0]["name"] == "ix_posts_title"
    assert result["dialect"] == ["alembic"]


def test_alembic_drop_table_and_execute_are_modeled(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "alembic/versions/abc123_init.py": """
from alembic import op
import sqlalchemy as sa

revision = "abc123"
down_revision = None

def upgrade():
    op.create_table('legacy', sa.Column('id', sa.Integer(), primary_key=True))
    op.execute("CREATE TABLE audit (id INTEGER PRIMARY KEY, note TEXT);")
    op.drop_table('legacy')
"""
        },
    )
    result = extract_schema(repo, ["alembic/versions"])
    table_names = [t["name"] for t in result["tables"]]
    assert "legacy" not in table_names
    assert "audit" in table_names


def test_alembic_drop_column_alter_column_drop_index_rename_table(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "alembic/versions/abc123_init.py": """
from alembic import op
import sqlalchemy as sa

revision = "abc123"
down_revision = None

def upgrade():
    op.create_table('accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=50), nullable=True),
        sa.Column('legacy_flag', sa.Boolean()),
    )
    op.create_index('ix_accounts_name', 'accounts', ['name'])
    op.drop_column('accounts', 'legacy_flag')
    op.alter_column('accounts', 'name', nullable=False, type_=sa.String(length=100))
    op.drop_index('ix_accounts_name', table_name='accounts')
    op.rename_table('accounts', 'users')
"""
        },
    )
    result = extract_schema(repo, ["alembic/versions"])
    table_names = [t["name"] for t in result["tables"]]
    assert "users" in table_names
    assert "accounts" not in table_names
    users = next(t for t in result["tables"] if t["name"] == "users")
    names = [c["name"] for c in users["columns"]]
    assert "legacy_flag" not in names
    name_col = next(c for c in users["columns"] if c["name"] == "name")
    assert name_col["nullable"] is False
    assert name_col["type"] == "STRING"
    assert result["indexes"] == []


def test_alembic_drop_constraint_stays_unsupported(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "alembic/versions/abc123_init.py": """
from alembic import op

revision = "abc123"
down_revision = None

def upgrade():
    op.drop_constraint('fk_accounts_user_id', 'accounts', type_='foreignkey')
"""
        },
    )
    events, _ = extract_alembic_migrations(repo, ["alembic/versions"])
    assert len(events) == 1
    assert events[0]["kind"] == "unsupported"
    assert "drop_constraint" in events[0]["statement"]


# ---------------------------------------------------------------------------
# extract_schema integration: dialect list + no cross-parser interference
# ---------------------------------------------------------------------------


def test_extract_schema_reports_dialect_list(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "app/migrations/0001_initial.py": """
from django.db import migrations, models

class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(name='Thing', fields=[('id', models.AutoField(primary_key=True))]),
    ]
"""
        },
    )
    result = extract_schema(repo, ["app/migrations"])
    assert result["dialect"] == ["django"]

    empty = extract_schema(repo, [])
    assert empty["dialect"] is None
