"""Extends citation_verifier.py's file-existence check with a real
line-bounds check against an actual local checkout (not AIR evidence
— the benchmark harness has direct filesystem access, unlike the
shipped product's evidence schema)."""
from pathlib import Path


def verify_findings_against_checkout(findings: list[dict], checkout_dir: Path) -> dict:
    verified = []
    unverified = []
    checkout_dir = Path(checkout_dir).resolve()
    for finding in findings:
        file_path = finding.get("file")
        line = finding.get("line")
        if not file_path:
            unverified.append(finding)
            continue
        full_path = (checkout_dir / file_path).resolve()
        # Ensure the resolved path is actually within checkout_dir (no escapes)
        if not full_path.is_relative_to(checkout_dir):
            unverified.append(finding)
            continue
        if not full_path.is_file():
            unverified.append(finding)
            continue
        if line is not None:
            line_count = sum(1 for _ in full_path.open())
            if line < 1 or line > line_count:
                unverified.append(finding)
                continue
        verified.append(finding)

    return {
        "total_findings": len(findings),
        "verified": verified,
        "unverified": unverified,
        "grounding_rate": (len(verified) / len(findings)) if findings else None,
    }
