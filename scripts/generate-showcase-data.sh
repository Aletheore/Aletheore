#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

run_aletheore() {
  PYTHONPATH="$ROOT_DIR/prototype" python3 -m aletheore.cli scan "$1"
}

echo "=== Django (full clone, pinned commit) ==="
git clone https://github.com/django/django.git "$WORKDIR/django"
git -C "$WORKDIR/django" checkout 3d34265d5d1b83fee5df3c1b6f55087b1a6a1ded
time run_aletheore "$WORKDIR/django"
cp "$WORKDIR/django/.aletheore/evidence.json" "$WORKDIR/django-evidence.json"

echo "=== Express (full clone, pinned commit) ==="
git clone https://github.com/expressjs/express.git "$WORKDIR/express"
git -C "$WORKDIR/express" checkout ae6dd37680e3a00618d6c8a3e522f0ee4eeba1a4
time run_aletheore "$WORKDIR/express"
cp "$WORKDIR/express/.aletheore/evidence.json" "$WORKDIR/express-evidence.json"
cp "$WORKDIR/express/.aletheore/evidence.toon" "$WORKDIR/express-evidence.toon"

echo "=== Kubernetes (shallow clone, pinned commit) ==="
git clone --depth 1 https://github.com/kubernetes/kubernetes.git "$WORKDIR/kubernetes"
git -C "$WORKDIR/kubernetes" fetch --depth 1 origin bd1a1b897340ef91595c36439fed49b9072f8b1d
git -C "$WORKDIR/kubernetes" checkout bd1a1b897340ef91595c36439fed49b9072f8b1d
KUBERNETES_SCAN_START=$(date +%s)
run_aletheore "$WORKDIR/kubernetes"
KUBERNETES_SCAN_END=$(date +%s)
echo "kubernetes scan seconds: $((KUBERNETES_SCAN_END - KUBERNETES_SCAN_START))" \
  | tee "$WORKDIR/kubernetes-scan-seconds.txt"
cp "$WORKDIR/kubernetes/.aletheore/evidence.json" "$WORKDIR/kubernetes-evidence.json"

python3 "$ROOT_DIR/scripts/extract-showcase-data.py" \
  --django "$WORKDIR/django-evidence.json" \
  --express "$WORKDIR/express-evidence.json" \
  --express-toon "$WORKDIR/express-evidence.toon" \
  --kubernetes "$WORKDIR/kubernetes-evidence.json" \
  --kubernetes-scan-seconds "$WORKDIR/kubernetes-scan-seconds.txt" \
  --out "$ROOT_DIR/website/showcase-data.js"

echo "Wrote website/showcase-data.js"
