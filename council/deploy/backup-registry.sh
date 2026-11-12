#!/bin/sh
# Copy the registry's data (database AND signing key) out of its volume into one dated, owner-only archive.
# The signing key is the important part: clients pin its public half, so a lost key means every client must be updated.
# Store the archive OFF this machine. Needs: docker. Writes only into ./backups.
set -eu
dest="${1:-./backups}"
mkdir -p "$dest"
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker run --rm -v council-registry_registry_data:/data:ro -v "$(cd "$dest" && pwd)":/out alpine:3.20 \
  tar czf "/out/registry-$stamp.tgz" -C /data .
echo "wrote $dest/registry-$stamp.tgz"
