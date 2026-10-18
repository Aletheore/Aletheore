#!/bin/bash
set -euf -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/src:${PYTHONPATH:-}"
exec "${SCRIPT_DIR}/../run_python_with_deps/run.sh" "${SCRIPT_DIR}/src/main.py" "$@"
