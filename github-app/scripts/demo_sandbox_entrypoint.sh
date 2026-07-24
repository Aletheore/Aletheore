#!/bin/sh
# Entrypoint for the gVisor-sandboxed demo-scan container (Dockerfile.demo-sandbox).
# Clones exactly one repo (URL already validated by the caller before this
# container is even started) and runs only the deterministic scan phase -
# no LLM calls, no OSV.dev lookup. stdout carries nothing but the final
# air.json evidence so the orchestrator can read it straight from the
# container's captured output; everything else goes to stderr.
set -eu

REPO_URL="$1"

git clone -q --depth 1 --single-branch "$REPO_URL" /work/repo 1>&2
aletheore scan /work/repo --no-check-vulnerabilities 1>&2
cat /work/repo/.aletheore/air.json
