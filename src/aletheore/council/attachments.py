"""Images people attach to what they type.

An image is identified by the SHA-256 of its bytes and travels through the room server as a blob; events carry
only a small reference. Types are decided by the file's own bytes (never its name), SVG is refused because it can
carry scripts, and every machine re-checks the bytes after downloading before an agent is ever pointed at the file.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS = 4
MAX_BLOBS_PER_ROOM = 100
MAX_ROOM_BLOB_BYTES = 100 * 1024 * 1024


def sniff_image(data: bytes) -> tuple[str, str] | None:
    """(mime, extension) for PNG, JPEG, GIF and WebP content; None for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def load_image(path: Path) -> tuple[str, bytes, str]:
    """(display name, bytes, mime) for an image file, or ValueError saying what is wrong with it."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise ValueError(f"no such file: {path}")
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ValueError(f"{path.name} is too large ({size // 1024} KB; the limit is {MAX_IMAGE_BYTES // 1024 // 1024} MB)")
    data = path.read_bytes()
    kind = sniff_image(data)
    if kind is None:
        raise ValueError(f"{path.name} is not a supported image (png, jpg, gif or webp)")
    return path.name[:80], data, kind[0]


def upload_image(blobs, room_id: str, path: Path) -> dict:
    name, data, mime = load_image(path)
    sha = hashlib.sha256(data).hexdigest()
    blobs.put_blob(room_id, sha, Path(path).expanduser())
    return {"name": name, "sha256": sha, "size": len(data), "mime": mime}


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(str(name)).name)[:60].strip(".") or "image"


def fetch_attachment(blobs, room_id: str, ref: dict, dest_dir: Path) -> Path:
    """Download one attachment into `dest_dir` and return its path. The bytes must match the announced hash and
    still be a supported image; the file name is derived from the hash, so a reference cannot choose where it lands."""
    sha = ref.get("sha256") if isinstance(ref, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("attachment has no valid sha-256")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f"{sha}.part"
    try:
        try:
            blobs.get_blob(room_id, sha, tmp)
        except KeyError:
            raise ValueError("attachment is no longer available on the server (images are kept for 7 days)") from None
        data = tmp.read_bytes()
        if len(data) > MAX_IMAGE_BYTES or hashlib.sha256(data).hexdigest() != sha:
            raise ValueError("attachment does not match its sha-256")
        kind = sniff_image(data)
        if kind is None:
            raise ValueError("attachment is not a supported image")
        final = dest_dir / f"{sha[:16]}-{_safe_name(Path(str(ref.get('name', 'image'))).stem)}.{kind[1]}"
        tmp.replace(final)
        return final
    finally:
        tmp.unlink(missing_ok=True)
