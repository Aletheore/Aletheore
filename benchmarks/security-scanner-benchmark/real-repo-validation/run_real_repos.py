"""Scans 20 real, already-vetted open-source repos (see fetch_repos.sh)
with find_secrets() and check_vulnerabilities() - the same functions
production uses - to get real false-positive-rate and coverage numbers
the small synthetic pilot corpus (../secrets, ../vulnerabilities) can't
provide on its own. See ../REPORT.md's "Real-repo validation" section
for results and analysis (two rounds: 11 repos, then 9 more added to
cover ecosystems/repo shapes the first round didn't touch).

Run from a scratch directory after fetch_repos.sh has populated it with
the 20 repo trees (never run this against a copy checked into this git
repo - these are third-party sources).
"""
import json
import sys
import time
from pathlib import Path

from aletheore.secrets import find_secrets
from aletheore.vulnerabilities import check_vulnerabilities

ROOT = Path(__file__).resolve().parent
REPOS = [
    "flask", "requests", "click", "express", "lodash", "axios", "cobra", "gin", "gorilla-mux", "gson", "junit4",
    "clap", "sinatra", "laravel", "restsharp", "okhttp", "penny-bot", "django", "client-go", "react",
]

results = {}
for name in REPOS:
    repo_path = ROOT / name
    if not repo_path.is_dir():
        print(f"skip {name}: not found", file=sys.stderr)
        continue

    t0 = time.time()
    secrets_result = find_secrets(repo_path)
    t1 = time.time()

    cache_path = ROOT / f".vuln-cache-{name}.json"
    vuln_result = check_vulnerabilities(repo_path, cache_path=cache_path)
    t2 = time.time()

    results[name] = {
        "scanned_files": secrets_result["scanned_files"],
        "secrets_findings": secrets_result["findings"],
        "secrets_seconds": round(t1 - t0, 2),
        "vuln_checked": vuln_result["checked"],
        "vuln_reason": vuln_result["reason"],
        "vuln_findings": vuln_result["findings"],
        "vuln_seconds": round(t2 - t1, 2),
    }
    print(
        f"{name:<15} files={secrets_result['scanned_files']:<5} "
        f"secrets_findings={len(secrets_result['findings']):<3} "
        f"vuln_findings={len(vuln_result['findings']):<4} "
        f"checked={vuln_result['checked']}"
    )

(ROOT / "results.json").write_text(json.dumps(results, indent=2))
print("\nWrote results.json")
