"""Real, single-file enclosing-scope lookup - parsed fresh from a file's
actual content, never from possibly-stale scan evidence
(evidence_resolution.find_symbol_at_location reads repository.modules,
which can predate or postdate the code actually under review; see
scan_worker/flash_review_hunk_scope.py for why that distinction matters
for a PR's real diff content specifically).

Scoped to Ruby and Python: the two languages this codebase's single-file
parsing already covers (orm_migrations.py's _rb_parser/_py_parser), and
the two involved in the real false positive this module exists to fix
(a `has_many` call the reviewing model believed was nested inside a
sibling Ruby exception class it was never actually inside, verified false
against the real file). Unsupported languages return None, same as
evidence_resolution.find_symbol_at_location's "never a guess" contract.
"""

from __future__ import annotations

import ast

from aletheore.orm_migrations import _rb_parser

_RUBY_SCOPE_NODE_TYPES = {"class", "module", "singleton_class"}


def _ruby_enclosing_scope(content: str, line: int) -> str | None:
    try:
        tree = _rb_parser().parse(content.encode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - malformed/truncated source, never a guess
        return None
    candidates: list[tuple[int, str]] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in _RUBY_SCOPE_NODE_TYPES:
            name_node = node.child_by_field_name("name")
            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            if name_node is not None and start <= line <= end:
                candidates.append((end - start, name_node.text.decode("utf-8", errors="replace")))
        stack.extend(node.children)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _python_enclosing_scope(content: str, line: int) -> str | None:
    try:
        tree = ast.parse(content)
    except SyntaxError:  # truncated/malformed source, never a guess
        return None
    candidates: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            end = getattr(node, "end_lineno", None)
            if end is not None and node.lineno <= line <= end:
                candidates.append((end - node.lineno, node.name))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def enclosing_scope_for_line(file_path: str, content: str, line: int) -> str | None:
    """The innermost real class/module name containing this line, parsed
    directly from this exact content - never a guess, and never from
    separately-scanned evidence that might describe a different point in
    time than this content. Returns None for an unsupported extension, a
    module-level location, or unparseable content."""
    if file_path.endswith(".rb"):
        return _ruby_enclosing_scope(content, line)
    if file_path.endswith(".py"):
        return _python_enclosing_scope(content, line)
    return None
