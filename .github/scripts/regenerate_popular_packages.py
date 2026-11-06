"""Rebuild src/aletheore/data/popular_packages.json, byte for byte, from its two pinned public sources.

The file is the offline list Shield's near-miss check compares new package names against. It is never edited by hand:

- PyPI: the first 1000 `project` names, in order, of hugovk/top-pypi-packages' top-pypi-packages.min.json at commit
  e6b5b398 ("Fix data for September", last_update 2026-10-01 12:40:51).
- npm: the first 1000 names, in order, of wooorm/npm-high-impact's lib/top.js at commit 6ca16535.

Usage: python .github/scripts/regenerate_popular_packages.py [--check]
  --check  exit 1 when the committed file differs from the rebuilt one (nothing is written).
To move to newer data, change the two commit SHAs and the date below, run without --check, and commit the result.
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

PYPI_COMMIT = "e6b5b398c1fcc783f593031f99bc2c772d64c6ef"
NPM_COMMIT = "6ca165357f4cf1e127f38065455fc1c7680f8b16"
PYPI_URL = f"https://raw.githubusercontent.com/hugovk/top-pypi-packages/{PYPI_COMMIT}/top-pypi-packages.min.json"
NPM_URL = f"https://raw.githubusercontent.com/wooorm/npm-high-impact/{NPM_COMMIT}/lib/top.js"
GENERATED = "2026-10-28"
TARGET = Path(__file__).resolve().parents[2] / "src" / "aletheore" / "data" / "popular_packages.json"


def _get(url: str) -> bytes:
    # B310: fixed https://raw.githubusercontent.com URLs above, never a file: or custom scheme
    with urllib.request.urlopen(url, timeout=60) as response:  # nosec B310
        return response.read()


def build() -> str:
    pypi_data = json.loads(_get(PYPI_URL))
    pypi = [row["project"] for row in pypi_data["rows"][:1000]]
    npm = re.findall(r"^\s*'([^']+)',?\s*$", _get(NPM_URL).decode("utf-8"), re.M)[:1000]
    source = (
        "PyPI: hugovk/top-pypi-packages (https://hugovk.github.io/top-pypi-packages/top-pypi-packages.min.json, "
        f"last_update {pypi_data['last_update']}, ClickHouse download counts), top 1000 by downloads. "
        "npm: wooorm/npm-high-impact (https://github.com/wooorm/npm-high-impact, lib/top.js), first 1000 entries. "
        "Both fetched programmatically; names only, in popularity order."
    )
    return json.dumps({"source": source, "generated": GENERATED, "pypi": pypi, "npm": npm}, indent=0) + "\n"


def main() -> int:
    rebuilt = build()
    if "--check" in sys.argv[1:]:
        same = TARGET.read_text(encoding="utf-8") == rebuilt
        print("popular_packages.json matches its sources" if same else "popular_packages.json differs from its sources")
        return 0 if same else 1
    TARGET.write_text(rebuilt, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
