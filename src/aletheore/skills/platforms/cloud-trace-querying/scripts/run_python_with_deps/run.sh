#!/bin/bash
# run.sh
# Launcher for running Python scripts with managed dependencies.
# Allows scripts to use PyPI dependencies without global installation.

set -euf -o pipefail

# Prevent Python from prepending the current working directory to sys.path.
# This protects against local directories overshadowing core/standard library modules.
export PYTHONSAFEPATH=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"

# Detect if we are running under the offline agent evaluation framework.
TEMP_DIR="${RUNTIME_ENV_DIR:-${TMP_DIR:-${TMPDIR:-/tmp}}}"
TESTING_DIR="${GCP_TRACE_MOCK_DIR:-}"

if [[ -z "${TESTING_DIR}" || ! -d "${TESTING_DIR}/mock_gcp" ]]; then
  CURR_DIR="${SCRIPT_DIR}"
  while [[ "${CURR_DIR}" != "/" && "${CURR_DIR}" != "." ]]; do
    if [[ -d "${CURR_DIR}/_internal/mock_gcp" ]]; then
      TESTING_DIR="${CURR_DIR}/_internal"
      break
    elif [[ -d "${CURR_DIR}/testing/gcp-trace-querying/mock_gcp" ]]; then
      TESTING_DIR="${CURR_DIR}/testing/gcp-trace-querying"
      break
    elif [[ -d "${CURR_DIR}/mock_gcp" ]]; then
      TESTING_DIR="${CURR_DIR}"
      break
    fi
    CURR_DIR="$(dirname "${CURR_DIR}")"
  done
fi

IS_MOCK_REQUESTED=false
if [[ "${USE_MOCK_GCP:-false}" == "true" ]]; then
  IS_MOCK_REQUESTED=true
elif [[ "${USE_MOCK_GCP:-false}" != "false" ]]; then
  IS_MOCK_REQUESTED=false
elif compgen -G "${TEMP_DIR}/*mock_trace_state*.json" > /dev/null 2>&1 || compgen -G "${TEMP_DIR}/.*mock_trace_state*.json" > /dev/null 2>&1; then
  IS_MOCK_REQUESTED=true
fi

if [[ "${IS_MOCK_REQUESTED}" == "true" && -n "${TESTING_DIR}" && -d "${TESTING_DIR}/mock_gcp" ]]; then
  MOCK_GCP_DIR="${TESTING_DIR}/mock_gcp"
  if [[ "${1:-}" == -* ]]; then
    TARGET_SRC_DIR=""
  else
    TARGET_SRC_DIR="$(cd "$(dirname "${1:-}")" >/dev/null 2>&1 && pwd || true)"
  fi
  export PYTHONPATH="${TARGET_SRC_DIR}:${MOCK_GCP_DIR}:${TESTING_DIR}:${TESTING_DIR}/shared:${PYTHONPATH:-}"
  exec python3 "$@"
fi


exec python3 "${SCRIPT_DIR}/src/manage_venv.py" "$@"
