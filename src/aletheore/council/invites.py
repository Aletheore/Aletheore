"""Invitation links.

A link looks like `https://<server>/j/<invite_id>#<secret>.<host device id>`:

* `<invite_id>` names the invitation on the server.
* `<secret>` (32 random bytes, base64url) is the key that unwraps the room key the host attached to the invitation. It sits
  after the `#`, which browsers and HTTP clients never send, and Council's own client never puts it in a request. The server
  stores only the wrapped blob, which is useless without the secret.
* `<host device id>` pins who the host is: the guest's client checks that the server's record of the host has exactly the keys
  that hash to this id. A recycled address or a look-alike server cannot present the real host's keys under another key.
  (Proof that the server really holds the host's key, rather than repeating public keys, comes with the host-signed events of
  Task 8 and the tunnel registry of Task 21.)
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from aletheore.council import crypto
from aletheore.council.crypto import CryptoError
from aletheore.council.identity import PublicIdentity

_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_DEVICE_RE = re.compile(r"dev_[0-9a-f]{24}")
_WRAP_CONTEXT = "council/invite-wrap/v1"
DEFAULT_TTL_S = 24 * 3600


@dataclass(frozen=True)
class InviteLink:
    url: str
    invite_id: str


@dataclass(frozen=True)
class ParsedLink:
    server: str
    invite_id: str
    secret: bytes
    host_device_id: str


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("the link's key part is not valid")
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != text:
        raise ValueError("the link's key part is not valid")  # spare bits set: not a link we made
    return raw


def build_link(server: str, invite_id: str, secret: bytes, host_device_id: str = "") -> str:
    fragment = _b64(secret) + (("." + host_device_id) if host_device_id else "")
    return f"{server.rstrip('/')}/j/{invite_id}#{fragment}"


def parse_link(url: str) -> ParsedLink:
    """Raises ValueError (with a message that never repeats the secret) for anything that is not a Council invitation link."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise ValueError("that is not a Council invitation link") from None
    segs = parts.path.split("/")
    if parts.scheme not in ("http", "https") or not parts.netloc or len(segs) != 3 or segs[1] != "j" or not _ID_RE.fullmatch(segs[2]):
        raise ValueError("that is not a Council invitation link")
    if not parts.fragment:
        raise ValueError("this link is missing its key part (the text after #); copy the whole link")
    secret_text, _, device = parts.fragment.partition(".")
    secret = _unb64(secret_text)
    if len(secret) != 32:
        raise ValueError("the link's key part has the wrong length; copy the whole link")
    if device and not _DEVICE_RE.fullmatch(device):
        raise ValueError("the link's host part is not valid")
    return ParsedLink(f"{parts.scheme}://{parts.netloc}", segs[2], secret, device)


def wrap_room_key(secret: bytes, room_key: bytes) -> bytes:
    nonce, ct = crypto.aead_encrypt(crypto.derive_key(secret, _WRAP_CONTEXT), room_key, b"council/invite")
    return nonce + ct


def unwrap_room_key(secret: bytes, blob: bytes) -> bytes:
    if len(blob) < crypto.NONCE_BYTES + 16:
        raise CryptoError("the wrapped key is too short")
    return crypto.aead_decrypt(crypto.derive_key(secret, _WRAP_CONTEXT), blob[:crypto.NONCE_BYTES], blob[crypto.NONCE_BYTES:], b"council/invite")


_CLAIM_CONTEXT = "council/invite-claim/v1"


def claim_key(secret: bytes) -> bytes:
    """What a guest shows to claim an invitation: derived from the link's secret, so the invitation's id alone (which travels in the visible
    part of the link and can leak through previews and logs) is not enough. The server keeps only its hash."""
    return crypto.derive_key(secret, _CLAIM_CONTEXT)


def claim_verifier(secret: bytes) -> bytes:
    return hashlib.sha256(claim_key(secret)).digest()


def make_invite(store, server: str, identity, room_id: str, key_provider: Callable[[], bytes] | None = None,
                ttl: int = DEFAULT_TTL_S) -> InviteLink:
    """Host side. `identity` is the host's device identity (its id goes into the link as the pin), or None for a token host.
    `key_provider` returns the room key to hand to the guest; None until a room is end-to-end encrypted (Task 9)."""
    secret = crypto.random_bytes(32)
    wrapped = wrap_room_key(secret, key_provider() if key_provider else b"")
    made = store.create_invite(room_id, wrapped, ttl, claim_verifier(secret))
    return InviteLink(build_link(server, made["invite_id"], secret, identity.device_id if identity is not None else ""), made["invite_id"])


def check_host_pin(pinned_device_id: str, host: dict | None) -> None:
    """The host record the server returned must be the pinned host: the keys it carries must hash to the id in the link."""
    if not pinned_device_id:
        return  # a link from a token-mode host carries no pin
    if not host:
        raise ValueError("the server has no record of the host this link names; do not join")
    try:
        public = PublicIdentity(bytes.fromhex(host["sign_vk"]), bytes.fromhex(host["box_pk"]))
    except (KeyError, ValueError, TypeError):
        raise ValueError("the server's record of the host is malformed; do not join") from None
    if public.device_id != pinned_device_id or host.get("device_id") != pinned_device_id:
        raise ValueError("this server is not the host's: the host's keys do not match the link. Do not join.")


def check_server_proves_host(base_url: str, pinned_device_id: str, http=None) -> bool:
    """Ask the server to sign a fresh nonce with the host's device key and check it against the host in the link. A server that
    cannot do this (an older one, or not a host's own machine) is allowed to pass: the pin check and the key in the link's fragment
    still apply. A server that answers with the WRONG key is refused. Returns True only when the proof was checked and good."""
    import httpx

    from aletheore.council import crypto
    from aletheore.council.tunnel_registry import verify_host_proof

    if not pinned_device_id:
        return False
    nonce = crypto.random_bytes(16)
    client = http or httpx.Client(base_url=base_url, timeout=15)
    try:
        resp = client.get("/v1/host-proof", params={"nonce": nonce.hex()})
    except httpx.HTTPError:
        raise ValueError("could not reach the server to check it is the host's; try again") from None
    if resp.status_code == 404:
        return False
    if resp.status_code != 200:
        raise ValueError("the server would not prove it is the host's; do not join")
    try:
        data = resp.json()
        public = PublicIdentity(bytes.fromhex(data["sign_vk"]), bytes.fromhex(data["box_pk"]))
        good = verify_host_proof(public, nonce, bytes.fromhex(data["signature"]))
    except (KeyError, ValueError, TypeError):
        raise ValueError("the server's proof is malformed; do not join") from None
    if public.device_id != pinned_device_id or not good:
        raise ValueError("this server is not the host's: it could not prove it holds the host's key. Do not join.")
    return True
