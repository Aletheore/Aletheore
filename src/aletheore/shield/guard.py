"""Assemble the Shield guard: new-dependency checks, then evidence checks, and render a short report.

Facts only, with counts and names, no advice. Anything skipped is reported in `not_checked`; a report with
neither findings nor skipped checks is the only "clean" one. `run_guard` never raises.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aletheore.shield import state
from aletheore.shield.deps import DepRef, added_dependencies
from aletheore.shield.gitutil import run_git
from aletheore.shield.guard_evidence import Finding, evidence_checks
from aletheore.shield.nearmiss import near_miss
from aletheore.shield.registry import lookup_package

LOOKUP_TIMEOUT_S = 3.0
MIN_LOOKUP_S = 0.25  # below this much remaining budget a lookup is not started
MAX_LOOKUPS = 10
NEW_PACKAGE_DAYS = 30
_REGISTRY_LABEL = {"PyPI": "PyPI", "npm": "npm"}
_CHECK_ORDER = ("dependencies", "affected", "signatures", "risks")
_SKIPPED_REASONS_SHOWN = 2
_clock = time.monotonic


@dataclass(frozen=True)
class GuardReport:
    findings: tuple[Finding, ...]
    not_checked: tuple[str, ...]


def _paths(out: bytes) -> list[str]:
    return [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]


def _changed_files(repo: Path, base: str) -> list[str]:
    """Files differing from `base` plus untracked ones; with no usable base every file counts as changed."""
    has_base = run_git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode == 0
    if has_base:
        tracked = run_git(repo, "diff", "--name-only", "-z", base)
    else:
        tracked = run_git(repo, "ls-files", "-z", "--cached")
    untracked = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    seen: dict[str, None] = {}
    for result in (tracked, untracked):
        if result.returncode == 0:
            for path in _paths(result.stdout):
                seen.setdefault(path)
    return list(seen)


def _plain(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _trivial_difference(name: str, popular: str) -> bool:
    """Separator-only or plural-only differences are not typosquat evidence."""
    a, b = _plain(name), _plain(popular)
    return a == b or a == b + "s" or b == a + "s"


def _near_miss(ecosystem: str, name: str) -> str | None:
    popular = near_miss(ecosystem, name)
    if popular and not _trivial_difference(name, popular):
        return popular
    return None


def _count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _days_ago(moment: datetime, now: datetime) -> int:
    return max(0, round((now - moment).total_seconds() / 86400))


def _dependency_findings(
    refs: list[DepRef], lookups: bool, deadline: float, opener: Callable | None, not_checked: list[str]
) -> list[Finding]:
    findings: list[Finding] = []
    now = datetime.now(timezone.utc)
    unique: dict[tuple[str, str], DepRef] = {}
    for ref in sorted(refs, key=lambda r: r.source != "manifest"):  # manifest entries first
        unique.setdefault((ref.ecosystem, ref.name), ref)
    ordered = list(unique.values())

    to_look = ordered[:MAX_LOOKUPS] if lookups else []
    skipped_cap = len(ordered) - len(to_look) if lookups else 0
    failures: dict[str, int] = {}
    looked: dict[tuple[str, str], object] = {}
    out_of_time = 0
    for index, ref in enumerate(to_look):
        remaining = deadline - _clock()
        if remaining < MIN_LOOKUP_S:
            out_of_time = len(to_look) - index
            break
        looked[(ref.ecosystem, ref.name)] = lookup_package(
            ref.ecosystem, ref.name, timeout=min(LOOKUP_TIMEOUT_S, remaining), opener=opener
        )

    for ref in ordered:
        label = _REGISTRY_LABEL.get(ref.ecosystem, ref.ecosystem)
        info = looked.get((ref.ecosystem, ref.name))
        exists = getattr(info, "exists", None)
        first = getattr(info, "first_published", None)
        popular = _near_miss(ref.ecosystem, ref.name)
        recent = first is not None and (now - first).total_seconds() < NEW_PACKAGE_DAYS * 86400
        age = f"first published {_days_ago(first, now)} days ago" if recent else ""

        if info is not None and exists is None:
            reason = getattr(info, "reason", None) or "registry could not be reached"
            failures[reason] = failures.get(reason, 0) + 1
        if exists is False:
            if ref.source == "manifest":
                findings.append(Finding("dependencies", "warn", f"{ref.name} does not exist on {label}.", ref.where))
            else:
                findings.append(
                    Finding(
                        "dependencies",
                        "info",
                        f"no {label} package is named '{ref.name}' (import names can differ from package names).",
                        ref.where,
                    )
                )
        if popular:
            corroborated = exists is False or recent
            note = ""
            if exists is False:
                note = f"; no {label} package of that name exists"
            elif recent:
                note = f"; {age}"
            elif info is None or exists is None:
                note = "; registry was not checked"
            findings.append(
                Finding(
                    "dependencies",
                    "warn" if corroborated else "info",
                    f"{ref.name} is one edit from the popular package {popular}{note}.",
                    ref.where,
                )
            )
        elif recent:
            findings.append(Finding("dependencies", "info", f"{ref.name} was first published {_days_ago(first, now)} days ago.", ref.where))

    if not lookups and ordered:
        not_checked.append(
            f"registry lookups are off, so {_count(len(ordered), 'new dependency name', 'new dependency names')} "
            "were not checked for existence or age"
        )
    if skipped_cap:
        not_checked.append(f"{skipped_cap} more names not looked up (limit {MAX_LOOKUPS} per run)")
    if out_of_time:
        not_checked.append(f"time budget reached; {out_of_time} names not looked up")
    for reason, count in failures.items():
        not_checked.append(f"registry lookup failed for {_count(count, 'name', 'names')}: {reason}")
    return findings


def run_guard(
    repo: Path,
    *,
    base: str = "HEAD",
    lookups: bool | None = None,
    budget_s: float = 2.0,
    base_evidence: dict | None = None,
    opener: Callable | None = None,
) -> GuardReport:
    repo = Path(repo)
    deadline = _clock() + budget_s
    findings: list[Finding] = []
    not_checked: list[str] = []
    try:
        enabled = state.registry_lookups_enabled(repo) if lookups is None else bool(lookups)
        changed = _changed_files(repo, base)

        refs, unchecked = added_dependencies(repo, base)
        by_reason: dict[str, int] = {}
        for item in unchecked:
            by_reason[item.reason] = by_reason.get(item.reason, 0) + 1
        for reason, count in by_reason.items():
            not_checked.append(f"{_count(count, 'dependency', 'dependencies')} not checked: {reason}")

        findings.extend(_dependency_findings(refs, enabled, deadline, opener, not_checked))

        evidence_findings, evidence_skipped = evidence_checks(repo, changed, base_evidence)
        findings.extend(evidence_findings)
        not_checked.extend(evidence_skipped)
    except Exception as exc:  # noqa: BLE001 - the guard must never break a hook
        not_checked.append(f"guard stopped on an unexpected error ({type(exc).__name__})")
    return GuardReport(tuple(findings), tuple(dict.fromkeys(not_checked)))


def _line(finding: Finding) -> str:
    message = " ".join(finding.message.split())
    evidence = " ".join(finding.evidence.split())
    text = f"{finding.severity}: {message}"
    return f"{text} ({evidence})" if evidence else text


def render_report(report: GuardReport, *, max_lines: int = 12) -> str:
    if not report.findings and not report.not_checked:
        return ""
    ordered = sorted(
        report.findings,
        key=lambda f: (_CHECK_ORDER.index(f.check) if f.check in _CHECK_ORDER else len(_CHECK_ORDER), f.severity != "warn"),
    )
    skipped_line = ""
    if report.not_checked:
        shown = "; ".join(report.not_checked[:_SKIPPED_REASONS_SHOWN])
        extra = len(report.not_checked) - _SKIPPED_REASONS_SHOWN
        skipped_line = f"not checked: {shown}" + (f" (+{extra} more)" if extra > 0 else "")
    room = max(max_lines - (1 if skipped_line else 0), 0)
    lines = [_line(f) for f in ordered]
    if len(lines) > room:
        keep = max(room - 1, 0)
        lines = lines[:keep] + ([f"+{len(ordered) - keep} more"] if room else [])
    if skipped_line:
        lines.append(skipped_line)
    return "\n".join(lines)
