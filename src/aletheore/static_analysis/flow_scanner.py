"""Aletheore's own data-flow scanner: finds tainted flows into known sinks without an external tool.

It runs the flow engine in discover mode (see aletheore/flow/discover.py): request data is followed
through the code, across methods and files, and a finding is reported where it reaches a SQL, command,
file, output, LDAP, XPath, session, deserialization, redirect or request-forgery sink without being validated
or encoded for it. Java and Python.
"""

import os
from pathlib import Path

from aletheore.flow.discover import discover_repo, has_source
from aletheore.static_analysis._cwe import add_cwe
from aletheore.static_analysis._exclusions import filter_findings

_DISABLE_ENV = "ALETHEORE_DISABLE_FLOW_DISCOVERY"
_OFF = {"1", "true", "yes", "on"}


def check_flow(repo_path: Path, timeout: int | None = None) -> dict:
    if os.environ.get(_DISABLE_ENV, "").strip().lower() in _OFF:
        return {"checked": False, "reason": f"flow discovery disabled ({_DISABLE_ENV} is set)", "findings": []}
    if not has_source(repo_path):
        return {"checked": True, "reason": None, "findings": [], "not_applicable": "no Java, Python, JavaScript, PHP, Go, C# or Ruby source files"}
    findings = []
    for f in discover_repo(repo_path):
        findings.append(add_cwe({k: v for k, v in f.items() if k != "cwe"}, f.get("cwe")))
    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
