"""Shared CWE normalization for the subprocess scanners.

Every scanner reports a weakness class in its own shape: Semgrep puts a string or list under
rule metadata ("CWE-89: Improper Neutralization..."), Bandit an `issue_cwe` object, Bearer a
`cwe_ids` list of bare numbers, gosec a `cwe` object. Normalizing them to one list of "CWE-<n>"
strings lets findings from different tools be compared by weakness class, which the rule ids
(each tool's own namespace) cannot do.

The field is optional: a finding with no CWE information simply has no `cwe` key, so output
for rules without CWE metadata is byte-for-byte what it was before.
"""

import re

_CWE_NUMBER = re.compile(r"CWE[-_ ]?(\d{1,5})", re.IGNORECASE)


def _numbers(value) -> list[int]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value] if value > 0 else []
    if isinstance(value, str):
        matches = _CWE_NUMBER.findall(value)
        if matches:
            return [int(m) for m in matches]
        # A bare number ("78"), as Bearer's cwe_ids use, but never free text that merely
        # contains digits.
        return [int(value)] if value.strip().isdigit() and int(value) > 0 else []
    if isinstance(value, dict):
        return _numbers(value.get("id"))
    if isinstance(value, (list, tuple, set)):
        return [n for item in value for n in _numbers(item)]
    return []


def normalize_cwes(*values) -> list[str]:
    """Sorted, de-duplicated "CWE-<n>" strings from any mix of the shapes above."""
    seen = {n for value in values for n in _numbers(value)}
    return [f"CWE-{n}" for n in sorted(seen)]


def add_cwe(finding: dict, *values) -> dict:
    """Set finding["cwe"] only when there is something to record; returns the finding."""
    cwes = normalize_cwes(*values)
    if cwes:
        finding["cwe"] = cwes
    return finding
