#!/bin/bash
set -euf -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTING_DIR="$(cd "${SCRIPT_DIR}/../../../../testing/gcp-trace-querying" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/src:${SCRIPT_DIR}/tests:${TESTING_DIR}:${TESTING_DIR}/mock_gcp:${TESTING_DIR}/shared:${PYTHONPATH:-}"
exec "${SCRIPT_DIR}/../run_python_with_deps/run.sh" -m unittest discover -s "${SCRIPT_DIR}/tests" -p "*_test.py"
