"""Deterministic database-schema extraction from Postgres DDL migrations.

Produces the `repository.database.schema` AIR section: the tables, columns,
foreign-key relations, and indexes a repository's migrations define, each
resolving to the file:line that introduced it.

Why a hand-written tokenizer rather than a grammar: the scanner ships no SQL
grammar (LANGUAGE_BY_EXTENSION covers 11 languages, none of them SQL), and
adding one would put a dialect-fragile dependency in the free CLI's install
path while still leaving the ALTER-replay logic to be written by hand. This
mirrors endpoints.py, which is purpose-built per-framework extraction for the
same reason.

Why not regex: a backtracking pattern over attacker-supplied SQL is the exact
shape of the ReDoS fixed in PR #190 (~23s for one crafted 29-character line).
Everything here is a single forward pass with no backtracking, so runtime is
linear in input size regardless of content.

Scope is deliberately Postgres-only for v1, and deliberately narrower than
Postgres: only the statements real migrations use to define shape. Anything
else is recorded in `unsupported` and skipped, never raised - a repository is
free to contain SQL this does not model, and a partial schema is more useful
than a failed scan.
"""

from __future__ import annotations

from pathlib import Path

class _Cursor:
    """A forward-only reader over one migration file.

    Tracks line numbers alongside offsets so every emitted table and column
    can cite the exact file:line it came from - the citation is the point of
    the whole section, so position tracking is not optional bookkeeping.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.line = 1

    def eof(self) -> bool:
        return self.pos >= len(self.text)

    def advance(self, count: int = 1) -> None:
        for _ in range(count):
            if self.pos >= len(self.text):
                return
            if self.text[self.pos] == "\n":
                self.line += 1
            self.pos += 1

    def peek(self, count: int = 1) -> str:
        return self.text[self.pos : self.pos + count]

    def skip_whitespace_and_comments(self) -> None:
        """Whitespace, `-- line` comments, and `/* block */` comments.

        Block comments do not nest in this implementation. Postgres allows
        nesting, but an unterminated or nested block comment only causes this
        to consume to EOF, which degrades to "no more statements in this
        file" - the failure mode is missing data, never a hang or a crash.
        """
        while not self.eof():
            char = self.text[self.pos]
            if char in " \t\r\n":
                self.advance()
            elif self.peek(2) == "--":
                while not self.eof() and self.text[self.pos] != "\n":
                    self.advance()
            elif self.peek(2) == "/*":
                self.advance(2)
                while not self.eof() and self.peek(2) != "*/":
                    self.advance()
                self.advance(2)
            else:
                return


def _read_identifier(cursor: _Cursor) -> str:
    """A bare or double-quoted identifier, optionally schema-qualified.

    Returns the *last* dotted segment: `public.users` and `users` are the
    same table, and treating them as two would split one table's columns
    across two entries in the diagram.
    """
    cursor.skip_whitespace_and_comments()
    parts: list[str] = []
    while True:
        if cursor.peek() == '"':
            cursor.advance()
            start = cursor.pos
            while not cursor.eof() and cursor.peek() != '"':
                cursor.advance()
            parts.append(cursor.text[start : cursor.pos])
            cursor.advance()
        else:
            start = cursor.pos
            while not cursor.eof() and (cursor.text[cursor.pos].isalnum() or cursor.text[cursor.pos] == "_"):
                cursor.advance()
            if cursor.pos == start:
                break
            parts.append(cursor.text[start : cursor.pos])
        if cursor.peek() == ".":
            cursor.advance()
            continue
        break
    return parts[-1] if parts else ""


def _skip_to_statement_end(cursor: _Cursor) -> None:
    """Consume through the next top-level `;`.

    Depth-aware and literal-aware: a semicolon inside parentheses, a quoted
    string, or a dollar-quoted body is not a statement terminator. Without
    this, a `DEFAULT ';'` or a function body would end the statement early
    and the rest of the file would be parsed as garbage.
    """
    depth = 0
    while not cursor.eof():
        char = cursor.text[cursor.pos]
        if char == "'":
            _skip_single_quoted(cursor)
            continue
        if char == "$":
            if _skip_dollar_quoted(cursor):
                continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            cursor.advance()
            return
        elif char == "-" and cursor.peek(2) == "--":
            cursor.skip_whitespace_and_comments()
            continue
        cursor.advance()


def _skip_single_quoted(cursor: _Cursor) -> None:
    # Postgres escapes a quote by doubling it ('it''s'), so a doubled quote
    # continues the literal rather than ending it.
    cursor.advance()
    while not cursor.eof():
        if cursor.peek() == "'":
            if cursor.peek(2) == "''":
                cursor.advance(2)
                continue
            cursor.advance()
            return
        cursor.advance()


def _skip_dollar_quoted(cursor: _Cursor) -> bool:
    """Skip a `$tag$ ... $tag$` body, returning whether one was found.

    Returns False for a bare `$` that is not a dollar-quote opener (e.g. a
    positional parameter like `$1`), leaving the cursor untouched so the
    caller can treat it as an ordinary character.
    """
    start = cursor.pos
    scan = cursor.pos + 1
    while scan < len(cursor.text) and (cursor.text[scan].isalnum() or cursor.text[scan] == "_"):
        scan += 1
    if scan >= len(cursor.text) or cursor.text[scan] != "$":
        return False
    tag = cursor.text[start : scan + 1]
    closing = cursor.text.find(tag, scan + 1)
    end = len(cursor.text) if closing == -1 else closing + len(tag)
    cursor.advance(end - cursor.pos)
    return True


def _split_top_level(body: str) -> list[str]:
    """Split a parenthesised column list on top-level commas.

    Depth- and literal-aware for the same reason as _skip_to_statement_end:
    `NUMERIC(10, 2)` and `DEFAULT 'a,b'` both contain commas that do not
    separate column definitions.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char == "'":
            end = index + 1
            while end < len(body):
                if body[end] == "'":
                    if body[end : end + 2] == "''":
                        end += 2
                        continue
                    break
                end += 1
            current.append(body[index : end + 1])
            index = end + 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if current:
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _tokenize_column_definition(text: str) -> list[str]:
    """Split one column definition into words, keeping a parenthesised group
    attached to the word before it.

    `NUMERIC(10, 2)` has to stay one token or the `, 2)` becomes a stray word
    and `DEFAULT` detection reads the wrong position. `DOUBLE PRECISION`
    stays two, which is why the type is accumulated rather than taken as a
    single token.
    """
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            end = index + 1
            while end < len(text):
                if text[end] == "'":
                    if text[end : end + 2] == "''":
                        end += 2
                        continue
                    break
                end += 1
            current.append(text[index : end + 1])
            index = end + 1
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char in " \t\r\n" and depth == 0:
            if current:
                tokens.append("".join(current))
                current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if current:
        tokens.append("".join(current))
    return tokens


_CONSTRAINT_STARTERS = frozenset(
    {"primary", "not", "null", "unique", "references", "default", "check", "generated", "collate", "constraint"}
)

# A table-level constraint rather than a column: `PRIMARY KEY (a, b)`,
# `FOREIGN KEY (x) REFERENCES ...`, `CONSTRAINT name ...`. These start with a
# keyword where a column name would be, which is how they are told apart.
_TABLE_CONSTRAINT_STARTERS = frozenset({"primary", "foreign", "unique", "constraint", "check", "exclude"})


def _strip_identifier(raw: str) -> str:
    return raw.strip().strip('"').split(".")[-1]


def _parse_references(tokens: list[str], start: int) -> tuple[str, str, str | None] | None:
    """`REFERENCES table(column) [ON DELETE action]` -> (table, column, action)."""
    if start + 1 >= len(tokens):
        return None
    target = tokens[start + 1]
    column = ""
    if "(" in target:
        table_part, _, rest = target.partition("(")
        column = rest.rstrip(")").strip()
        target = table_part
    elif start + 2 < len(tokens) and tokens[start + 2].startswith("("):
        column = tokens[start + 2].strip("()").strip()

    on_delete = None
    lowered = [token.lower() for token in tokens]
    for index in range(start, len(lowered) - 2):
        if lowered[index] == "on" and lowered[index + 1] == "delete":
            action_words = []
            for word in lowered[index + 2 :]:
                if word in {"on", "deferrable", "initially"}:
                    break
                action_words.append(word)
                if " ".join(action_words) in {"cascade", "restrict", "no action", "set null", "set default"}:
                    break
            if action_words:
                on_delete = " ".join(action_words).upper()
            break

    return _strip_identifier(target), _strip_identifier(column), on_delete


def _parse_column_definition(text: str, file: str, line: int) -> tuple[dict | None, dict | None]:
    """One column definition -> (column, relation).

    Returns (None, relation) for a table-level FOREIGN KEY, and (None, None)
    for any other table-level constraint - those carry no column of their
    own but may still carry an edge the diagram needs.
    """
    tokens = _tokenize_column_definition(text)
    if not tokens:
        return None, None

    lowered = [token.lower() for token in tokens]
    if lowered[0] in _TABLE_CONSTRAINT_STARTERS:
        if lowered[0] == "foreign" or (lowered[0] == "constraint" and "foreign" in lowered):
            try:
                ref_index = lowered.index("references")
            except ValueError:
                return None, None
            key_index = lowered.index("foreign")
            local_column = ""
            if key_index + 2 < len(tokens) and tokens[key_index + 2].startswith("("):
                local_column = _strip_identifier(tokens[key_index + 2].strip("()"))
            parsed = _parse_references(tokens, ref_index)
            if parsed is None:
                return None, None
            to_table, to_column, on_delete = parsed
            return None, {
                "from_column": local_column,
                "to_table": to_table,
                "to_column": to_column,
                "on_delete": on_delete,
                "file": file,
                "line": line,
            }
        return None, None

    name = _strip_identifier(tokens[0])
    type_words: list[str] = []
    index = 1
    while index < len(tokens) and lowered[index] not in _CONSTRAINT_STARTERS:
        type_words.append(tokens[index])
        index += 1

    column = {
        "name": name,
        "type": " ".join(type_words).upper() or "UNKNOWN",
        "primary_key": False,
        "nullable": True,
        "unique": False,
        "default": None,
        "file": file,
        "line": line,
    }

    relation = None
    cursor = index
    while cursor < len(tokens):
        word = lowered[cursor]
        if word == "primary" and cursor + 1 < len(lowered) and lowered[cursor + 1] == "key":
            column["primary_key"] = True
            # A PRIMARY KEY column is NOT NULL by definition in Postgres,
            # whether or not the migration spells it out - recording it as
            # nullable would make the diagram contradict the database.
            column["nullable"] = False
            cursor += 2
            continue
        if word == "not" and cursor + 1 < len(lowered) and lowered[cursor + 1] == "null":
            column["nullable"] = False
            cursor += 2
            continue
        if word == "unique":
            column["unique"] = True
            cursor += 1
            continue
        if word == "default":
            default_words = []
            scan = cursor + 1
            while scan < len(tokens) and lowered[scan] not in _CONSTRAINT_STARTERS:
                default_words.append(tokens[scan])
                scan += 1
            column["default"] = " ".join(default_words) or None
            cursor = scan
            continue
        if word == "references":
            parsed = _parse_references(tokens, cursor)
            if parsed is not None:
                to_table, to_column, on_delete = parsed
                relation = {
                    "from_column": name,
                    "to_table": to_table,
                    "to_column": to_column,
                    "on_delete": on_delete,
                    "file": file,
                    "line": line,
                }
            cursor += 1
            continue
        cursor += 1

    return column, relation


def _read_parenthesised_body(cursor: _Cursor) -> str | None:
    """The text between a balanced `( ... )`, cursor left after the close."""
    cursor.skip_whitespace_and_comments()
    if cursor.peek() != "(":
        return None
    cursor.advance()
    start = cursor.pos
    depth = 1
    while not cursor.eof() and depth > 0:
        char = cursor.text[cursor.pos]
        if char == "'":
            _skip_single_quoted(cursor)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                break
        cursor.advance()
    body = cursor.text[start : cursor.pos]
    cursor.advance()
    return body


def _match_keywords(cursor: _Cursor, words: list[str]) -> bool:
    """Whether the next tokens are these keywords, consuming them if so.

    Position is restored on a partial match, so a failed probe for
    `CREATE UNIQUE INDEX` leaves the cursor free to probe `CREATE INDEX`.
    """
    saved_pos, saved_line = cursor.pos, cursor.line
    for word in words:
        cursor.skip_whitespace_and_comments()
        start = cursor.pos
        while not cursor.eof() and (cursor.text[cursor.pos].isalnum() or cursor.text[cursor.pos] == "_"):
            cursor.advance()
        if cursor.text[start : cursor.pos].lower() != word:
            cursor.pos, cursor.line = saved_pos, saved_line
            return False
    return True


def _parse_file(text: str, rel_path: str) -> dict:
    """Every shape-defining statement in one migration file, in file order."""
    cursor = _Cursor(text)
    events: list[dict] = []
    unsupported: list[dict] = []

    while True:
        cursor.skip_whitespace_and_comments()
        if cursor.eof():
            break
        line = cursor.line

        if _match_keywords(cursor, ["create", "table"]):
            _match_keywords(cursor, ["if", "not", "exists"])
            name = _read_identifier(cursor)
            body = _read_parenthesised_body(cursor)
            _skip_to_statement_end(cursor)
            if name and body is not None:
                events.append({"kind": "create_table", "table": name, "body": body, "line": line})
            continue

        if _match_keywords(cursor, ["alter", "table"]):
            _match_keywords(cursor, ["if", "exists"])
            _match_keywords(cursor, ["only"])
            name = _read_identifier(cursor)
            if _match_keywords(cursor, ["add", "column"]):
                _match_keywords(cursor, ["if", "not", "exists"])
                start = cursor.pos
                _skip_to_statement_end(cursor)
                definition = cursor.text[start : cursor.pos].rstrip(";").strip()
                if name and definition:
                    events.append(
                        {"kind": "add_column", "table": name, "body": definition, "line": line}
                    )
                continue
            # Any other ALTER (DROP COLUMN, RENAME, ALTER TYPE, ADD
            # CONSTRAINT): recorded rather than guessed at. v1 models only
            # the operations this repo's own migrations actually use.
            start = cursor.pos
            _skip_to_statement_end(cursor)
            unsupported.append(
                {
                    "file": rel_path,
                    "line": line,
                    "statement": _summarize(f"ALTER TABLE {name} {cursor.text[start : cursor.pos]}"),
                }
            )
            continue

        is_unique = _match_keywords(cursor, ["create", "unique", "index"])
        if is_unique or _match_keywords(cursor, ["create", "index"]):
            _match_keywords(cursor, ["concurrently"])
            _match_keywords(cursor, ["if", "not", "exists"])
            index_name = _read_identifier(cursor)
            table = ""
            if _match_keywords(cursor, ["on"]):
                table = _read_identifier(cursor)
            _match_keywords(cursor, ["using", "btree"])
            body = _read_parenthesised_body(cursor)
            _skip_to_statement_end(cursor)
            if index_name and table:
                events.append(
                    {
                        "kind": "create_index",
                        "table": table,
                        "name": index_name,
                        "body": body or "",
                        "unique": is_unique,
                        "line": line,
                    }
                )
            continue

        start = cursor.pos
        _skip_to_statement_end(cursor)
        statement = cursor.text[start : cursor.pos].strip()
        if statement and statement != ";":
            unsupported.append(
                {"file": rel_path, "line": line, "statement": _summarize(statement)}
            )

    return {"events": events, "unsupported": unsupported}


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
        parsed = _parse_file(text, rel_path)
        unsupported.extend(parsed["unsupported"])

        for event in parsed["events"]:
            if event["kind"] == "create_table":
                # CREATE TABLE IF NOT EXISTS on a table an earlier migration
                # already created is a no-op in Postgres, so it must be one
                # here too - re-applying it would duplicate every column.
                if event["table"] in tables:
                    continue
                columns: list[dict] = []
                for definition in _split_top_level(event["body"]):
                    column, relation = _parse_column_definition(definition, rel_path, event["line"])
                    if column is not None:
                        columns.append(column)
                    if relation is not None:
                        relations.append({"from_table": event["table"], **relation})
                tables[event["table"]] = {
                    "name": event["table"],
                    "columns": columns,
                    "file": rel_path,
                    "line": event["line"],
                }
            elif event["kind"] == "add_column":
                table = tables.get(event["table"])
                if table is None:
                    # An ALTER against a table no CREATE was seen for: the
                    # migration set is partial (a squashed baseline, or a
                    # table created outside these directories). Recorded
                    # rather than inventing a table with one column, which
                    # would render as a phantom node in the diagram.
                    unsupported.append(
                        {
                            "file": rel_path,
                            "line": event["line"],
                            "statement": _summarize(
                                f"ALTER TABLE {event['table']} ADD COLUMN on an unknown table"
                            ),
                        }
                    )
                    continue
                column, relation = _parse_column_definition(event["body"], rel_path, event["line"])
                if column is not None and not any(c["name"] == column["name"] for c in table["columns"]):
                    table["columns"].append(column)
                if relation is not None:
                    relations.append({"from_table": event["table"], **relation})
            elif event["kind"] == "create_index":
                index_columns = [
                    _strip_identifier(part.split()[0]) if part.split() else ""
                    for part in _split_top_level(event["body"])
                ]
                indexes.append(
                    {
                        "name": event["name"],
                        "table": event["table"],
                        "columns": [c for c in index_columns if c],
                        "unique": event["unique"],
                        "file": rel_path,
                        "line": event["line"],
                    }
                )

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
        "dialect": "postgresql",
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
