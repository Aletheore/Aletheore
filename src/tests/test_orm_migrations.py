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


def test_django_add_field_and_add_index_and_unsupported(tmp_path):
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
        migrations.RemoveField(model_name='post', name='old_field'),
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
    assert len(result["unsupported"]) == 1
    assert "RemoveField" in result["unsupported"][0]["statement"]
    assert result["dialect"] == ["django"]


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
    assert len(result["unsupported"]) == 1
    assert "remove_column" in result["unsupported"][0]["statement"]


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

def upgrade():
    op.create_table('accounts', sa.Column('id', sa.Integer(), primary_key=True))
""",
            "alembic/versions/def456_add_posts.py": """
from alembic import op
import sqlalchemy as sa

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


def test_alembic_unsupported_ops_recorded(tmp_path):
    repo = write_files(
        tmp_path,
        {
            "alembic/versions/abc123_init.py": """
from alembic import op

def upgrade():
    op.drop_table('legacy')
    op.execute("UPDATE accounts SET active = true")
"""
        },
    )
    events, _ = extract_alembic_migrations(repo, ["alembic/versions"])
    assert len(events) == 2
    assert all(e["kind"] == "unsupported" for e in events)


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
