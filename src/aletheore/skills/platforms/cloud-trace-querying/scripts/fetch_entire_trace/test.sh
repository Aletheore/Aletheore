#!/bin/bash
set -euf -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/src:${SCRIPT_DIR}/tests:${PYTHONPATH:-}"
exec "${SCRIPT_DIR}/../run_python_with_deps/run.sh" -m unittest discover -s "${SCRIPT_DIR}/tests" -p "*_test.py"
