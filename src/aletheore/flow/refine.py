"""Apply flow validation to a scanner's raw findings and report what was suppressed.

A finding is suppressed only when the flow engine PROVES its sink value safe (or the sink
unreachable). Nothing is silently deleted: every suppressed finding is returned with a reason so it
can be listed in the evidence and audited. Any failure inside the engine keeps the finding.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from aletheore.flow.cs_index import CsIndex
from aletheore.flow.discover_kt import validate_kt_finding
from aletheore.flow.discover_rs import validate_rs_finding
from aletheore.flow.rs_index import RsIndex
from aletheore.flow.discover_rb import validate_rb_finding
from aletheore.flow.kt_index import KtIndex
from aletheore.flow.rb_index import RbIndex
from aletheore.flow.discover_cs import validate_cs_finding
from aletheore.flow.discover_go import validate_go_finding
from aletheore.flow.discover_php import validate_php_finding
from aletheore.flow.go_index import GoIndex
from aletheore.flow.java_index import JavaIndex
from aletheore.flow.php_index import EXTS as PHP_EXTS, PhpIndex
from aletheore.flow.validate import TAINT_CWES, validate_finding

_DISABLE_ENV = "ALETHEORE_DISABLE_FLOW_REFINEMENT"
_OFF = {"1", "true", "yes", "on"}
MAX_FINDINGS = 20000
MAX_SECONDS = 120.0


def refinement_disabled() -> bool:
    return os.environ.get(_DISABLE_ENV, "").strip().lower() in _OFF


def cwes_from_metadata(metadata: object) -> set[int]:
    raw = (metadata or {}).get("cwe") if isinstance(metadata, dict) else None
    values = raw if isinstance(raw, list) else [raw] if raw else []
    return {int(n) for v in values for n in re.findall(r"CWE-(\d+)", str(v))}


def refine_semgrep_results(repo_path: Path, results: list[dict], relative_path) -> tuple[list[dict], list[dict]]:
    """Split Semgrep's raw results into (kept, suppressed). `suppressed` entries are plain records."""
    if refinement_disabled() or not results:
        return results, []
    kept: list[dict] = []
    suppressed: list[dict] = []
    index: JavaIndex | None = None
    php_index: PhpIndex | None = None
    go_index: GoIndex | None = None
    cs_index: CsIndex | None = None
    rb_index: RbIndex | None = None
    kt_index: KtIndex | None = None
    rs_index: RsIndex | None = None
    started = time.monotonic()
    evaluated = 0
    for item in results:
        try:
            path = item.get("path", "") if isinstance(item, dict) else ""
            extra = item.get("extra") or {}
            cwes = cwes_from_metadata(extra.get("metadata"))
            eligible = (
                path.endswith((".java", ".go", ".cs", ".rb", ".kt", ".rs") + tuple(PHP_EXTS))
                and bool(cwes & TAINT_CWES)
                and evaluated < MAX_FINDINGS
                and time.monotonic() - started < MAX_SECONDS
            )
            if not eligible:
                kept.append(item)
                continue
            rel = relative_path(path, repo_path)
            start, end = item.get("start") or {}, item.get("end") or {}
            span = ((int(start.get("line", 0)), int(start.get("col", 1))), (int(end.get("line", 0)), int(end.get("col", 1))))
            if path.endswith(".java"):
                if index is None:
                    index = JavaIndex(repo_path)
                verdict = validate_finding(repo_path, rel, span[0], span[1], cwes, index)
            elif path.endswith(".go"):
                if go_index is None:
                    go_index = GoIndex(repo_path)
                verdict = validate_go_finding(repo_path, rel, span[0], span[1], cwes, go_index)
            elif path.endswith(".rs"):
                if rs_index is None:
                    rs_index = RsIndex(repo_path)
                verdict = validate_rs_finding(repo_path, rel, span[0], span[1], cwes, rs_index)
            elif path.endswith(".kt"):
                if kt_index is None:
                    kt_index = KtIndex(repo_path)
                verdict = validate_kt_finding(repo_path, rel, span[0], span[1], cwes, kt_index)
            elif path.endswith(".rb"):
                if rb_index is None:
                    rb_index = RbIndex(repo_path)
                verdict = validate_rb_finding(repo_path, rel, span[0], span[1], cwes, rb_index)
            elif path.endswith(".cs"):
                if cs_index is None:
                    cs_index = CsIndex(repo_path)
                verdict = validate_cs_finding(repo_path, rel, span[0], span[1], cwes, cs_index)
            else:
                if php_index is None:
                    php_index = PhpIndex(repo_path)
                verdict = validate_php_finding(repo_path, rel, span[0], span[1], cwes, php_index)
            evaluated += 1
        except Exception:  # noqa: BLE001 - refinement must never break a scan or hide a finding
            kept.append(item)
            continue
        if verdict.droppable:
            suppressed.append(
                {
                    "tool": "semgrep",
                    "rule_id": item.get("check_id", ""),
                    "path": rel,
                    "line": int(start.get("line", 0)),
                    "cwe": [f"CWE-{n}" for n in sorted(cwes)],
                    "reason": verdict.detail or verdict.kind,
                }
            )
        else:
            kept.append(item)
    return kept, suppressed
