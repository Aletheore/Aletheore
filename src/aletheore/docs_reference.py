"""Grounded API-reference markdown, rendered directly from AIR evidence -
no LLM call, no invented prose. A symbol with no extracted docstring is
rendered as explicitly undocumented rather than given a guessed
description, matching the same grounding contract citation_verifier and
the audit report already enforce elsewhere in this codebase.
"""

UNDOCUMENTED = "*Undocumented - no docstring found.*"


def _render_signature(symbol: dict) -> str:
    params = symbol["params"] if symbol["params"] is not None else ""
    signature = f"{symbol['name']}{params}"
    if symbol.get("return_type"):
        signature += f" -> {symbol['return_type']}"
    return signature


def _render_symbol(symbol: dict, module_path: str) -> str:
    lines = [
        f"### `{_render_signature(symbol)}`",
        "",
        symbol["docstring"] if symbol.get("docstring") else UNDOCUMENTED,
        "",
        f"`{module_path}:{symbol['start_line']}`",
    ]
    return "\n".join(lines)


def build_module_reference(evidence: dict, module_path: str) -> str:
    """Markdown API reference for one module's public symbols. Raises
    ValueError for a module path not present in evidence, matching the
    error style of query.py's other target-requiring lookups.
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
            sections.append(_render_symbol(cls, module_path))
            sections.append("")
    if functions:
        sections.append("## Functions")
        sections.append("")
        for func in functions:
            sections.append(_render_symbol(func, module_path))
            sections.append("")
    if not classes and not functions:
        sections.append("*No public symbols found.*")
        sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def build_api_reference(evidence: dict) -> dict[str, str]:
    """Module path -> rendered markdown, for every module with at least
    one public function or class. Modules with no public surface (empty
    files, purely-private helpers) are omitted rather than rendered as
    empty stubs.
    """
    reference: dict[str, str] = {}
    for module in evidence["repository"]["modules"]:
        has_public_symbol = any(
            symbol.get("is_public", True)
            for symbol in module["symbols"]["classes"] + module["symbols"]["functions"]
        )
        if has_public_symbol:
            reference[module["path"]] = build_module_reference(evidence, module["path"])
    return reference
