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


def build_combined_reference(modules: dict[str, str], repo_full_name: str) -> str:
    """Every module from build_api_reference concatenated into one markdown
    document with a table of contents, instead of a dict the caller has to
    render module-by-module - the single-file form for exporting or
    committing the reference, as opposed to the dashboard's per-module view.
    """
    title = f"# API Reference — {repo_full_name}\n"
    if not modules:
        return title + "\nNo public functions or classes found yet.\n"

    paths = sorted(modules)
    toc = "\n".join(f"- [{path}](#{_github_heading_anchor(path)})" for path in paths)
    sections = "\n\n---\n\n".join(modules[path].rstrip() for path in paths)
    return f"{title}\n## Contents\n\n{toc}\n\n---\n\n{sections}\n"
