"""Vetting checks for curated skills entering the bundled skill-hub catalog.

This module gates which skills are trustworthy enough to ship as part of
Aletheore's bundled catalog. It runs three independent checks:

1. ``check_license`` -- a license-compliance allowlist check.
2. ``check_instruction_text`` -- a **pattern lint**, not a security check.
   It matches a fixed list of known-bad instruction shapes (piping a remote
   download into a shell, phrasing that asks to send a secret/credential to
   a URL). It catches exactly the specific shapes it was written for and
   nothing else: it is NOT a general prompt-injection detector, it will not
   catch novel phrasing, and it is not a substitute for human review. A
   clean/empty result from this check must never be read as "this skill's
   instructions were security-reviewed" -- it only means none of the known-bad
   patterns matched.
3. ``check_bundled_scripts`` -- a **real, substantive security check**. It
   runs any bundled ``scripts/`` directory through Aletheore's own existing
   secrets scanner (the same scan-engine entry point that backs the
   ``aletheore_secrets`` MCP tool), so this is actual evidence-grounded
   scanning, not a reimplementation and not a heuristic lint.

``vet_skill_directory`` is the single entry point: it reads a skill
directory's ``SKILL.md``, runs all three checks, and returns the
concatenated list of failures (an empty list means the skill passes).

Deliberately independent of ``skill_hub.py``: this module does its own tiny
bit of YAML-frontmatter splitting rather than importing
``skill_hub._parse_skill_file`` (a private helper of Section A/B), so Section
C's vetting pipeline can be built and tested with zero dependency on that
module landing first.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from aletheore.secrets import find_secrets

ALLOWED_LICENSES = frozenset({"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"})

# Deliberately duplicated here rather than imported from skill_hub.py -- see
# the module docstring for why Section C stays independent of Section A/B.
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


@dataclass
class VettingFailure:
    reason: str
    detail: str


def check_license(license_id: str | None) -> VettingFailure | None:
    """Reject a skill whose declared license isn't on the allowlist.

    A missing license (``None``) is treated the same as an unknown one --
    both fail.
    """
    if license_id in ALLOWED_LICENSES:
        return None
    return VettingFailure(
        reason="disallowed_license",
        detail=f"license {license_id!r} is not in the allowlist {sorted(ALLOWED_LICENSES)}",
    )


# Known-bad instruction-text shapes. This is a pattern LINT, not a security
# scanner: it only matches the specific shapes listed below (case-insensitive)
# and makes no claim about anything else, including obfuscated variants of
# these same shapes. See the module docstring for the full caveat.
SUSPICIOUS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "unsafe_piped_execution",
        re.compile(r"\b(curl|wget)\b[^\n|]{0,200}\|\s*(sh|bash|zsh|python3?|perl|ruby)\b", re.IGNORECASE),
    ),
    (
        "credential_exfiltration_phrasing",
        re.compile(
            r"\b(send|exfiltrat\w*|upload|transmit|post)\b[\s\S]{0,80}"
            r"\b(api[ _-]?key|secret|credential\w*|password|token)\b[\s\S]{0,120}"
            r"https?://",
            re.IGNORECASE,
        ),
    ),
]


def check_instruction_text(markdown_body: str) -> list[VettingFailure]:
    """Pattern-lint a skill's instruction text for known-bad shapes.

    This is NOT a general prompt-injection detector and NOT a substitute for
    human review. It catches the specific known-bad shapes in
    ``SUSPICIOUS_PATTERNS`` (unsafe piped execution, credential-exfiltration
    phrasing) and nothing else -- novel phrasing or anything a competent
    attacker bothers to obfuscate will not be caught. A passing (empty)
    result means only "no known-bad pattern matched", never "this skill's
    instructions were security-reviewed".
    """
    failures: list[VettingFailure] = []
    for reason, pattern in SUSPICIOUS_PATTERNS:
        match = pattern.search(markdown_body)
        if match:
            failures.append(
                VettingFailure(
                    reason=reason,
                    detail=f"matched known-bad pattern {reason!r}: {match.group(0)!r}",
                )
            )
    return failures


def check_bundled_scripts(skill_dir: Path) -> list[VettingFailure]:
    """Run a skill's bundled ``scripts/`` directory through Aletheore's real scanner.

    Unlike ``check_instruction_text``, this IS a real, substantive security
    check: it reuses Aletheore's own existing secrets-scanning entry point
    (``aletheore.secrets.find_secrets``, the same scan-engine function behind
    the ``aletheore_secrets`` MCP tool), not a reimplementation or a
    heuristic lint. Returns ``[]`` if the skill has no ``scripts/``
    subdirectory.
    """
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        return []

    result = find_secrets(scripts_dir)
    failures: list[VettingFailure] = []
    for finding in result["findings"]:
        failures.append(
            VettingFailure(
                reason="secret_in_bundled_script",
                detail=f"{finding['path']}:{finding['line']} matched pattern {finding['pattern']!r}",
            )
        )
    return failures


EXEMPTIONS_FILE = "_vetting_exemptions.json"


def load_exemptions(skills_root: Path) -> dict[str, list[dict]]:
    """Hand-reviewed exemptions, keyed by skill id (``category/name``). A missing or unreadable file means none.

    Each entry clears ONE finding on ONE skill: it names the rule (``reason``), the text that must appear in the
    finding's detail (``detail_contains``) and why a person cleared it (``why``). An exemption without all three is
    ignored, so it can never act as a blanket pass.
    """
    try:
        data = json.loads((skills_root / EXEMPTIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, list[dict]] = {}
    for skill_id, entries in (data.get("exemptions") or {}).items():
        valid = [e for e in entries if isinstance(e, dict) and all(isinstance(e.get(k), str) and e[k].strip() for k in ("reason", "detail_contains", "why"))]
        if valid:
            out[skill_id] = valid
    return out


def split_exempted(skill_dir: Path, failures: list[VettingFailure]) -> tuple[list[VettingFailure], list[VettingFailure]]:
    """Split ``failures`` into (still failing, cleared by a hand-reviewed exemption) for a skill inside a skills tree."""
    skill_id = f"{skill_dir.parent.name}/{skill_dir.name}"
    entries = load_exemptions(skill_dir.parent.parent).get(skill_id, [])
    failing: list[VettingFailure] = []
    cleared: list[VettingFailure] = []
    for failure in failures:
        if any(e["reason"] == failure.reason and e["detail_contains"] in failure.detail for e in entries):
            cleared.append(failure)
        else:
            failing.append(failure)
    return failing, cleared


def vet_skill_directory(skill_dir: Path) -> list[VettingFailure]:
    """Run every vetting check against a skill directory.

    Findings that a person cleared by hand in ``_vetting_exemptions.json`` (next to the skills, one exact finding per
    entry, with the reason written down) are not returned; everything else is.

    Reads and parses ``SKILL.md``'s frontmatter to find the declared
    license; any read/parse failure (missing file, missing/malformed
    frontmatter, unparseable YAML) is treated the same as
    ``check_license(None)`` -- it fails the license check rather than
    raising. Returns the concatenation of every non-``None`` failure from
    ``check_license``, ``check_instruction_text``, and
    ``check_bundled_scripts``. An empty list means the skill passes.
    """
    license_id: str | None = None
    body = ""

    skill_md = skill_dir / "SKILL.md"
    try:
        text = skill_md.read_text()
        match = _FRONTMATTER.match(text)
        if match is not None:
            frontmatter = yaml.safe_load(match.group(1))
            body = match.group(2)
            if isinstance(frontmatter, dict):
                license_id = frontmatter.get("license")
    except (OSError, yaml.YAMLError):
        pass

    failures: list[VettingFailure] = []

    license_failure = check_license(license_id)
    if license_failure is not None:
        failures.append(license_failure)

    failures.extend(check_instruction_text(body))
    failures.extend(check_bundled_scripts(skill_dir))

    return split_exempted(skill_dir, failures)[0]
