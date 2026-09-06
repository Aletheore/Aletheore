"""Deterministic correction for a real Flash Review false-positive
mechanism: a diff hunk's header shows the nearest preceding class/module
signature as a git heuristic (readability only), not proof the hunk's
lines are still nested inside it. FLASH_REVIEW_SYSTEM_PROMPT now cautions
the model about this directly, but that caution alone measured as a
partial mitigation on a real re-run (2 of 3 repeats still misread it) -
this module closes the gap deterministically instead of relying on the
model to reliably self-correct: it re-parses each hunk's own file at the
hunk's real, fresh content (never possibly-stale scan evidence - see
aletheore.scope_lookup's own docstring) and, only when git's claimed
context genuinely disagrees with the real enclosing scope, states the
correction as a fact rather than leaving the model to reason about an
ambiguous header alone.

Deliberately silent (no fact emitted) when scope_lookup returns None
(unsupported language, module-level hunk, or unparseable content) or when
git's header agrees with the real scope - this is a targeted correction
for a proven failure mode, not a running commentary on every hunk.
"""

from __future__ import annotations

import re

from aletheore.scope_lookup import enclosing_scope_for_line

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@\s*(.*)$")
_HEADER_SCOPE_NAME_RE = re.compile(r"^(?:class|module)\s+([A-Za-z_][A-Za-z0-9_:]*)")

MAX_HUNK_SCOPE_BYTES = 4_000


def _claimed_scope_name(header_context: str) -> str | None:
    match = _HEADER_SCOPE_NAME_RE.match(header_context.strip())
    return match.group(1) if match else None


def _hunk_claims_with_changed_lines(patch: str) -> list[tuple[str, list[int]]]:
    """(claimed class/module name, new-file line numbers to check) for
    every hunk in this one file's patch whose header names a class/module
    - most hunks name a def or nothing at all and are skipped here, since
    this module only has a real scope_lookup to check a class/module claim
    against.

    Checks the hunk's own start line (the header's own positional claim)
    plus every added line in the hunk body - not just the start. A
    unified diff hunk can span a class/module boundary, so its first line
    agreeing with the header is not proof every changed line does: a real
    gap found via Flash Review's own review of this module (checking only
    the start line let a later added line genuinely inside a different,
    nested class slip through unflagged whenever the hunk's first line
    happened to agree with the header)."""
    found: list[tuple[str, list[int]]] = []
    current_line: int | None = None
    claimed: str | None = None
    changed: list[int] = []

    def flush() -> None:
        if claimed is not None:
            found.append((claimed, changed))

    for line in patch.splitlines():
        match = _HUNK_HEADER_RE.match(line)
        if match:
            flush()
            current_line = int(match.group(1))
            claimed = _claimed_scope_name(match.group(2))
            changed = [current_line] if claimed else []
            continue
        if current_line is None:
            continue
        if claimed is not None and line.startswith("+") and not line.startswith("+++"):
            changed.append(current_line)
        if not line.startswith("-"):
            current_line += 1
    flush()
    return found


def _joined(lines: list[str]) -> str:
    if not lines:
        return ""
    return "--- hunk-header scope corrections (verified against real file content) ---\n" + "\n".join(lines)


def build_hunk_scope_correction_context(
    file_contents: dict[str, str],
    diff_patches: tuple[tuple[str, str], ...] | None,
) -> str:
    if not diff_patches:
        return ""
    lines: list[str] = []

    def emit(line: str) -> bool:
        # Checks the real encoded size of the final joined output (header
        # + every line + the "\n" separators between them), not just this
        # line's own bytes - a running total of line bytes alone under-
        # counts by the header and every separator, letting the actual
        # returned context exceed MAX_HUNK_SCOPE_BYTES by that much.
        candidate = lines + [line]
        if len(_joined(candidate).encode("utf-8")) > MAX_HUNK_SCOPE_BYTES:
            return False
        lines.append(line)
        return True

    for file_path, patch in diff_patches:
        content = file_contents.get(file_path)
        if content is None:
            continue
        for claimed, changed_lines in _hunk_claims_with_changed_lines(patch):
            disagreement: tuple[int, str] | None = None
            for candidate_line in changed_lines:
                real = enclosing_scope_for_line(file_path, content, candidate_line)
                if real is not None and real != claimed:
                    disagreement = (candidate_line, real)
                    break
            if disagreement is None:
                continue
            hunk_line, real = disagreement
            line = (
                f"{file_path}:{hunk_line} - the diff hunk header nearby lists `{claimed}` as "
                f"context, but this location is actually inside `{real}` (verified against the real "
                f"file content; a hunk header shows only the nearest preceding signature, not proof "
                f"of enclosing scope)."
            )
            if not emit(line):
                return _joined(lines)
    return _joined(lines)
