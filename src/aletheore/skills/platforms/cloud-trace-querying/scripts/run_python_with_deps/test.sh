#!/bin/bash
set -euf -o pipefail
# Prevent Python from prepending the current working directory to sys.path.
# This protects against local directories overshadowing core/standard library modules.
export PYTHONSAFEPATH=1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
exec python3 -m unittest discover -s "${SCRIPT_DIR}/tests" -p "*_test.py"
