#!/bin/sh
# Entrypoint for the gVisor-sandboxed demo-scan container (Dockerfile.demo-sandbox).
# Clones exactly one repo (URL already validated by the caller before this
# container is even started) and runs only the deterministic scan phase -
# no LLM calls, no OSV.dev lookup. stdout carries nothing but the final
# air.json evidence so the orchestrator can read it straight from the
# container's captured output; everything else goes to stderr.
set -eu

REPO_URL="$1"

# This clones and scans an arbitrary public repo a stranger on the internet
# just typed into the website demo - the repo author fully controls its
# content, including any .aletheore/scan-cache.json they choose to commit.
# Without this, a poisoned cache entry (matching content hash, fabricated
# parse result) would be trusted verbatim instead of re-parsed, letting
# anyone make the demo show a fake "clean" result for their own repo. See
# aletheore.evidence's _DISABLE_LOCAL_SCAN_CACHE_ENV for the full reasoning
# - this is the same fix applied to the hosted scan-worker's own subprocess
# calls, applied here too since this path never goes through jobs.py at all.
export ALETHEORE_DISABLE_LOCAL_SCAN_CACHE=1

git clone -q --depth 1 --single-branch "$REPO_URL" /work/repo 1>&2
aletheore scan /work/repo --no-check-vulnerabilities 1>&2
cat /work/repo/.aletheore/air.json
