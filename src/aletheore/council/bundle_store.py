"""Where worker bundles are kept: the BundleStore interface and an on-disk implementation."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from aletheore.council.protocol import MAX_BUNDLE_BYTES

ROOM_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
OBJECT_MAGIC = b"CNCLOBJ1"  # the first bytes of an object an encrypted room's client sealed
OBJECT_SLACK = 64 * 1024  # framing and per-chunk overhead a sealed object adds to the plaintext size
OBJECT_MIN = 8 + 4 + 24  # magic, epoch, stream header


def check_ids(room_id: str, sha256: str) -> None:
    if not isinstance(room_id, str) or not ROOM_RE.match(room_id) or room_id in (".", ".."):
        raise ValueError("invalid room id")
    if not isinstance(sha256, str) or not SHA_RE.match(sha256):
        raise ValueError("bundle id must be a lowercase sha-256")


class BundleStore(Protocol):
    def put_bundle(self, room_id: str, sha256: str, path: Path) -> None: ...

    def get_bundle(self, room_id: str, sha256: str, dest: Path) -> None: ...

    def put_blob(self, room_id: str, sha256: str, path: Path) -> None: ...

    def get_blob(self, room_id: str, sha256: str, dest: Path) -> None: ...


class LocalBundleStore:
    """Bundles in a directory on this machine (single-machine rooms and tests)."""

    def __init__(self, directory: Path, opaque: bool = False) -> None:
        """`opaque` stores what it is given without looking inside (for objects an encrypted room's client already sealed)."""
        self.directory = Path(directory)
        self.opaque = opaque

    def _path(self, room_id: str, sha256: str) -> Path:
        check_ids(room_id, sha256)
        return self.directory / room_id / f"{sha256}.bundle"

    def put_bundle(self, room_id: str, sha256: str, path: Path) -> None:
        target = self._path(room_id, sha256)
        data = Path(path).read_bytes()
        if len(data) > MAX_BUNDLE_BYTES + (OBJECT_SLACK if self.opaque else 0):
            raise ValueError("bundle too large")
        if not self.opaque and hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("bundle does not match its sha-256")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def get_bundle(self, room_id: str, sha256: str, dest: Path) -> None:
        src = self._path(room_id, sha256)
        if not src.is_file():
            raise KeyError(f"no such bundle: {sha256}")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def put_blob(self, room_id: str, sha256: str, path: Path) -> None:
        from aletheore.council.attachments import MAX_IMAGE_BYTES, sniff_image

        check_ids(room_id, sha256)
        data = Path(path).read_bytes()
        if len(data) > MAX_IMAGE_BYTES + (OBJECT_SLACK if self.opaque else 0):
            raise ValueError("image too large")
        if not self.opaque:
            if hashlib.sha256(data).hexdigest() != sha256:
                raise ValueError("image does not match its sha-256")
            if sniff_image(data) is None:
                raise ValueError("not a supported image")
        target = self.directory / room_id / f"{sha256}.blob"
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def get_blob(self, room_id: str, sha256: str, dest: Path) -> None:
        check_ids(room_id, sha256)
        src = self.directory / room_id / f"{sha256}.blob"
        if not src.is_file():
            raise KeyError(f"no such image: {sha256}")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
