"""Grounded API-reference markdown, rendered directly from AIR evidence -
no LLM call, no invented prose. A symbol with no extracted docstring is
rendered as explicitly undocumented rather than given a guessed
description, matching the same grounding contract citation_verifier and
the audit report already enforce elsewhere in this codebase.

An optional `ai_descriptions` argument lets a caller (the hosted, paid-tier
Docs feature - see scan_worker/live_docs.py) supply already-generated,
already-verified text for symbols with no docstring, or a polished rewrite
of an existing one. This module stays LLM-free either way: it only ever
renders text handed to it, and always marks AI-touched text distinctly from
the developer's own verbatim words - never silently presented as source
comments that were never written.
"""

import inspect
import re

from aletheore.dead_code import is_test_file

UNDOCUMENTED = "*Undocumented - no docstring found.*"
AI_GENERATED_MARKER = "*(AI-generated - no docstring found in source)*"
AI_POLISHED_MARKER = "*(AI-polished from the original docstring)*"


def _render_signature(symbol: dict) -> str:
    signature = f"{symbol['name']}{symbol.get('params') or ''}"
    if symbol.get("return_type"):
        signature += f" -> {symbol['return_type']}"
    return signature


def _render_docstring(docstring: str | None) -> str:
    if not docstring:
        return UNDOCUMENTED
    # Evidence stores the raw extracted text, indentation and all (grounding
    # fidelity - it's a faithful capture of the source, not a display
    # string) - a multi-line docstring's continuation lines carry the
    # source's own indentation (e.g. a Python docstring indented to match
    # its function body). Left as-is, that indentation reads as a Markdown
    # code block (4+ leading spaces) instead of flowing prose. inspect.
    # cleandoc is the standard-library tool built for exactly this
    # docstring shape - strips it for display, here at the rendering layer,
    # without touching what's actually stored in evidence.
    return inspect.cleandoc(docstring)


def _render_symbol(symbol: dict, module_path: str, ai_descriptions: dict[str, dict] | None) -> str:
    ai_entry = (ai_descriptions or {}).get(symbol["name"])
    if ai_entry is not None:
        marker = AI_POLISHED_MARKER if ai_entry.get("mode") == "polished" else AI_GENERATED_MARKER
        body = f"{ai_entry['description']}\n\n{marker}"
    else:
        body = _render_docstring(symbol.get("docstring"))

    lines = [
        f"### `{_render_signature(symbol)}`",
        "",
        body,
        "",
        f"`{module_path}:{symbol['start_line']}`",
    ]
    return "\n".join(lines)


def build_module_reference(
    evidence: dict, module_path: str, ai_descriptions: dict[str, dict] | None = None
) -> str:
    """Markdown API reference for one module's public symbols. Raises
    ValueError for a module path not present in evidence, matching the
    error style of query.py's other target-requiring lookups.

    `ai_descriptions`, when given, is keyed by symbol name with
    {"description": str, "mode": "generated" | "polished"} values (the
    exact shape scan_worker.live_docs.generate_file_descriptions returns) -
    a symbol name absent from it renders exactly as it would with no
    `ai_descriptions` argument at all.
    """
    module = next(
        (m for m in evidence["repository"]["modules"] if m["path"] == module_path), None
    )
    if module is None:
        raise ValueError(f"{module_path} not found in evidence")

    classes = [c for c in module["symbols"]["classes"] if c.get("is_public", True)]
    functions = [f for f in module["symbols"]["functions"] if f.get("is_public", True)]

    sections = [f"# {module_path}", ""]
    if classes:
        sections.append("## Classes")
        sections.append("")
        for cls in classes:
            sections.append(_render_symbol(cls, module_path, ai_descriptions))
            sections.append("")
    if functions:
        sections.append("## Functions")
        sections.append("")
        for func in functions:
            sections.append(_render_symbol(func, module_path, ai_descriptions))
            sections.append("")
    if not classes and not functions:
        sections.append("*No public symbols found.*")
        sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def build_api_reference(
    evidence: dict, ai_descriptions_by_module: dict[str, dict[str, dict]] | None = None
) -> dict[str, str]:
    """Module path -> rendered markdown, for every module with at least
    one public function or class. Modules with no public surface (empty
    files, purely-private helpers) are omitted rather than rendered as
    empty stubs.
    """
    reference: dict[str, str] = {}
    for module in evidence["repository"]["modules"]:
        if is_test_file(module["path"]):
            continue
        has_public_symbol = any(
            symbol.get("is_public", True)
            for symbol in module["symbols"]["classes"] + module["symbols"]["functions"]
        )
        if has_public_symbol:
            ai_descriptions = (ai_descriptions_by_module or {}).get(module["path"])
            reference[module["path"]] = build_module_reference(
                evidence, module["path"], ai_descriptions
            )
    return reference


def _github_heading_anchor(heading: str) -> str:
    # Mirrors GitHub's own heading-to-anchor algorithm closely enough for a
    # generated document: lowercase, drop anything that isn't a letter,
    # digit, space, hyphen or underscore, then turn spaces into hyphens.
    slug = re.sub(r"[^\w\s-]", "", heading.lower())
    return re.sub(r"\s+", "-", slug.strip())


def _unique_github_heading_anchor(heading: str, seen_counts: dict[str, int]) -> str:
    """Like _github_heading_anchor, but deduplicated the same way GitHub
    itself deduplicates same-page anchors: the first heading that slugifies
    to a given anchor keeps the bare anchor, each later one gets -1, -2, ...
    appended.

    Real bug this closes: two distinct headings that differ only in
    characters the slugifier strips (e.g. module paths "src/a.b.py" and
    "src/ab.py" both becoming "srcabpy") produced identical TOC links -
    clicking either jumped to whichever heading GitHub happened to assign
    the bare anchor to, silently sending the other link to the wrong
    section. Callers must build one `seen_counts` dict and thread it
    through every anchor call for one document, in the same order the
    headings themselves will appear, so the numbering matches what GitHub
    will actually assign on render.
    """
    anchor = _github_heading_anchor(heading)
    count = seen_counts.get(anchor, 0)
    seen_counts[anchor] = count + 1
    return anchor if count == 0 else f"{anchor}-{count}"


def _escape_table_cell(value: str) -> str:
    # A literal `|` inside a table cell breaks the row into extra columns;
    # a real path/handler/type string is never expected to contain one, but
    # nothing upstream guarantees it (a route registered from a config file,
    # say), so this is defensive, not decorative. Only for plain (non-code-
    # span) cell content - see _code_span for values wrapped in backticks,
    # where a `|` needs no escaping (GitHub renders inline code as atomic
    # when splitting table cells) and a backtick can't be backslash-escaped
    # at all.
    return value.replace("|", "\\|")


def _code_span(value: str) -> str:
    """Wraps `value` in a Markdown inline code span using a fence long
    enough to contain it safely.

    Real Flash Review finding on the PR that introduced backtick
    escaping: backslash escapes do NOT work inside a code span (the
    CommonMark spec says content between backtick fences is verbatim), so
    `_escape_table_cell`'s old `` "`" -> "\\`" `` replacement never
    actually escaped a real backtick - it just left a literal backslash
    character next to the closing fence while the value's own backtick
    still closed the span early, producing malformed or misleading
    output. The fence is sized to one more backtick than the longest run
    already in `value`, with a padding space on each side when `value`
    starts or ends with a backtick (or is empty) - exactly what the
    CommonMark spec requires so the padding itself isn't read as content.
    A `|` inside the span needs no separate escaping: GitHub's table
    renderer treats inline code as an atomic unit when splitting a row
    into cells.
    """
    longest_run = 0
    current_run = 0
    for char in value:
        if char == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    fence = "`" * (longest_run + 1)
    if not value or value.startswith("`") or value.endswith("`"):
        return f"{fence} {value} {fence}"
    return f"{fence}{value}{fence}"


def _render_column(column: dict) -> str:
    constraints = []
    if column.get("primary_key"):
        constraints.append("PRIMARY KEY")
    if column.get("unique"):
        constraints.append("UNIQUE")
    if column.get("nullable") is False:
        constraints.append("NOT NULL")
    if column.get("default") is not None:
        constraints.append(f"DEFAULT {column['default']}")
    name = _code_span(column.get("name", ""))
    col_type = _escape_table_cell(column.get("type") or "")
    return f"| {name} | {col_type} | {_escape_table_cell(', '.join(constraints))} |"


def build_schema_reference(evidence: dict) -> str:
    """Markdown "Database Schema" section: every table's columns and every
    foreign-key relation, rendered directly from AIR evidence - no LLM
    call, no invented prose, matching this module's grounding contract.

    Returns "" if schema mapping wasn't run or found no tables, so a
    caller can omit the section entirely rather than render an empty
    heading - the same convention build_api_reference already uses for a
    module with no public symbols.

    A relation carries its own real `file`/`line` (the migration statement
    that created it) and is rendered as a citation. A table itself does
    too - the location of its own `create_table`/`CREATE TABLE` statement,
    always attached by schema_map.py's `_merge_schema_events` regardless
    of source (raw SQL, Django, Rails, or Alembic) - and is rendered the
    same way.
    """
    schema = evidence.get("repository", {}).get("database", {}).get("schema", {})
    if not schema.get("checked") or not schema.get("tables"):
        return ""

    relations_by_table: dict[str, list[dict]] = {}
    for relation in schema.get("relations", []):
        relations_by_table.setdefault(relation["from_table"], []).append(relation)

    def _render_relations(table_relations: list[dict]) -> None:
        sections.append("Foreign keys:")
        sections.append("")
        for relation in sorted(table_relations, key=lambda r: r["from_column"]):
            on_delete = ""
            if relation.get("on_delete"):
                on_delete_text = f"ON DELETE {relation['on_delete']}"
                on_delete = f" ({_code_span(on_delete_text)})"
            location = ""
            if relation.get("file") and relation.get("line"):
                location_text = f"{relation['file']}:{relation['line']}"
                location = f" \u2014 {_code_span(location_text)}"
            target_text = f"{relation['to_table']}.{relation['to_column']}"
            sections.append(
                f"- {_code_span(relation['from_column'])} \u2192 "
                f"{_code_span(target_text)}{on_delete}{location}"
            )
        sections.append("")

    sections = ["## Database Schema", ""]
    known_table_names = {table["name"] for table in schema["tables"]}
    for table in sorted(schema["tables"], key=lambda t: t["name"]):
        table_location = ""
        if table.get("file") and table.get("line"):
            location_text = f"{table['file']}:{table['line']}"
            table_location = f" \u2014 {_code_span(location_text)}"
        sections.append(f"### {_code_span(table['name'])}{table_location}")
        sections.append("")
        columns = table.get("columns") or []
        if columns:
            sections.append("| Column | Type | Constraints |")
            sections.append("|---|---|---|")
            sections.extend(_render_column(column) for column in columns)
            sections.append("")
        table_relations = relations_by_table.get(table["name"], [])
        if table_relations:
            _render_relations(table_relations)

    # A relation's from_table isn't guaranteed to have a matching
    # CREATE TABLE this scan detected - e.g. an ALTER TABLE ADD
    # CONSTRAINT on a table that predates the tracked migration
    # directory, or was created by a path this scanner doesn't parse.
    # Render those under their own heading instead of silently dropping
    # them, so every relation this scan found is accounted for somewhere.
    orphan_table_names = sorted(set(relations_by_table) - known_table_names)
    for table_name in orphan_table_names:
        sections.append(f"### {_code_span(table_name)}")
        sections.append("")
        _render_relations(relations_by_table[table_name])

    return "\n".join(sections).rstrip() + "\n"


def build_endpoints_reference(evidence: dict) -> str:
    """Markdown "API Endpoints" section: every resolved HTTP route,
    rendered directly from AIR evidence - no LLM call.

    Returns "" if endpoint mapping wasn't run or found no resolved routes,
    matching build_schema_reference's identical convention. `unresolved`
    endpoints (a route whose path could not be statically determined) are
    excluded - a reference doc with a literal "<unresolved>" path entry
    would be actively misleading, not merely incomplete.
    """
    api_endpoints = evidence.get("repository", {}).get("api_endpoints", {})
    if not api_endpoints.get("checked"):
        return ""
    endpoints = [e for e in api_endpoints.get("endpoints", []) if not e.get("unresolved")]
    if not endpoints:
        return ""

    sections = [
        "## API Endpoints", "",
        "| Method | Path | Handler | Location |",
        "|---|---|---|---|",
    ]
    for endpoint in sorted(endpoints, key=lambda e: (e["path"], e["method"])):
        method = _escape_table_cell(endpoint.get("method") or "")
        path = _code_span(endpoint.get("path") or "")
        handler = _code_span(endpoint.get("handler") or "")
        location = ""
        if endpoint.get("file") and endpoint.get("line") is not None:
            location = _code_span(f"{endpoint['file']}:{endpoint['line']}")
        sections.append(f"| {method} | {path} | {handler} | {location} |")
    sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def build_combined_reference(
    modules: dict[str, str], repo_full_name: str, evidence: dict | None = None
) -> str:
    """Every module from build_api_reference concatenated into one markdown
    document with a table of contents, instead of a dict the caller has to
    render module-by-module - the single-file form for exporting or
    committing the reference, as opposed to the dashboard's per-module view.

    `evidence`, when given, adds "API Endpoints" and "Database Schema"
    overview sections ahead of the per-module reference (whichever of the
    two actually have content - see build_endpoints_reference/
    build_schema_reference). Omitted (the default) reproduces the exact
    prior output with no overview sections, for callers that only have
    the rendered `modules` dict and not the raw evidence.
    """
    title = f"# API Reference \u2014 {repo_full_name}\n"

    overview_sections: list[tuple[str, str]] = []
    if evidence is not None:
        endpoints_md = build_endpoints_reference(evidence)
        if endpoints_md:
            overview_sections.append(("API Endpoints", endpoints_md))
        schema_md = build_schema_reference(evidence)
        if schema_md:
            overview_sections.append(("Database Schema", schema_md))

    if not modules and not overview_sections:
        return title + "\nNo public functions or classes found yet.\n"

    seen_anchors: dict[str, int] = {}
    toc_entries = [
        f"- [{heading}](#{_unique_github_heading_anchor(heading, seen_anchors)})"
        for heading, _ in overview_sections
    ]
    toc_entries += [
        f"- [{path}](#{_unique_github_heading_anchor(path, seen_anchors)})"
        for path in sorted(modules)
    ]
    toc = "\n".join(toc_entries)

    body_sections = [markdown.rstrip() for _, markdown in overview_sections]
    body_sections += [modules[path].rstrip() for path in sorted(modules)]
    body = "\n\n---\n\n".join(body_sections)

    return f"{title}\n## Contents\n\n{toc}\n\n---\n\n{body}\n"
