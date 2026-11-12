"""A device's identity: its keys, its public half, and the private key file on disk.

One device is one identity (decision 3 in the stage 3 plan). A person who owns two machines links them as two devices in
Task 5; nothing here knows about accounts. The key file lives in a private directory, is readable only by the user, and is
never replaced silently: a damaged file is an error, not a reason to make a new identity.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess  # nosec B404 - no shell: argument lists only
from dataclasses import dataclass
from pathlib import Path

import nacl.pwhash.argon2id as argon2id
import nacl.secret
import nacl.exceptions

from aletheore.council import crypto
from aletheore.council.crypto import CryptoError

KDF_OPS = argon2id.OPSLIMIT_MODERATE  # export strength; tests lower these
KDF_MEM = argon2id.MEMLIMIT_MODERATE
_MAX_KDF_MEM = 1 << 30  # an imported file may not ask for more than 1 GiB of memory
_MAX_KDF_OPS = 20
_EXPORT_MAGIC = b"CNCLKEY1"
_NAME_MAX = 64

WORDS = (
    "acorn", "actor", "agent", "album", "alder", "amber", "anchor", "angle", "apple", "arch", "arrow", "aspen",
    "atlas", "aura", "autumn", "badge", "baker", "barn", "basil", "beach", "beacon", "bean", "bear", "beetle",
    "bell", "berry", "birch", "bison", "blade", "blanket", "bloom", "boat", "bolt", "bone", "book", "boot", "bottle",
    "bridge", "brook", "brush", "bucket", "cabin", "cable", "cactus", "camel", "candle", "canoe", "canyon", "cape",
    "card", "cargo", "carrot", "castle", "cedar", "chain", "chalk", "charm", "cherry", "chess", "chime", "cider",
    "circle", "cliff", "clock", "cloud", "clover", "coal", "cobra", "coin", "comet", "copper", "coral", "cotton",
    "cove", "crab", "crane", "creek", "crest", "crow", "crown", "crystal", "daisy", "dawn", "delta", "desert", "dew",
    "diamond", "dock", "dolphin", "dome", "dove", "dragon", "drum", "dune", "eagle", "earth", "echo", "eel", "elm",
    "ember", "emerald", "engine", "falcon", "fawn", "feather", "fern", "ferry", "field", "finch", "fire", "fjord",
    "flame", "flint", "flute", "foam", "forest", "fox", "frost", "garden", "garnet", "gate", "ginger", "glacier",
    "globe", "goat", "gold", "grape", "grove", "gull", "harbor", "hare", "hawk", "hazel", "heron", "hill", "honey",
    "horizon", "hound", "iris", "island", "ivory", "jade", "jaguar", "jasmine", "jungle", "kayak", "kettle", "kite",
    "koala", "lagoon", "lake", "lamp", "lantern", "lark", "laurel", "lava", "leaf", "lemon", "lilac", "linen",
    "lion", "lotus", "lunar", "lynx", "maple", "marble", "meadow", "melon", "mesa", "meteor", "mint", "mist", "moon",
    "moss", "moth", "mountain", "nectar", "nest", "nickel", "night", "oak", "oasis", "ocean", "olive", "onyx",
    "opal", "orbit", "orchid", "otter", "owl", "panda", "pearl", "pebble", "pepper", "pine", "planet", "plum",
    "pond", "poppy", "prairie", "quail", "quartz", "quill", "rabbit", "rain", "raven", "reef", "ribbon", "ridge",
    "river", "robin", "rocket", "rose", "ruby", "saddle", "saffron", "sage", "sailor", "salmon", "sand", "sapphire",
    "shadow", "shell", "silver", "sky", "slate", "snow", "spark", "spruce", "star", "stone", "storm", "summit",
    "sun", "swan", "thistle", "thunder", "tiger", "timber", "topaz", "trail", "tulip", "tundra", "valley", "velvet",
    "violet", "walnut", "wave", "willow", "wind", "winter", "wolf", "wren", "yarrow", "zebra", "zephyr",
)


@dataclass(frozen=True)
class PublicIdentity:
    sign_vk: bytes
    box_pk: bytes

    @property
    def fingerprint(self) -> str:
        return crypto.fingerprint(self.sign_vk, self.box_pk)

    @property
    def device_id(self) -> str:
        return "dev_" + self.fingerprint[:24]

    def __repr__(self) -> str:
        return f"PublicIdentity(device_id={self.device_id!r})"

    __str__ = __repr__


class Identity:
    """The keys of one device. Secrets are held in private attributes and never printed, copied into logs or pickled."""

    def __init__(self, sign_sk: bytes, sign_vk: bytes, box_sk: bytes, box_pk: bytes) -> None:
        self._sign_sk, self._box_sk = sign_sk, box_sk
        self._public = PublicIdentity(sign_vk, box_pk)

    def public(self) -> PublicIdentity:
        return self._public

    @property
    def device_id(self) -> str:
        return self._public.device_id

    def sign(self, data: bytes) -> bytes:
        return crypto.sign(self._sign_sk, data)

    def derive_secret(self, context: str) -> bytes:
        """A secret only this device can compute, for one purpose (for example, encrypting its own key file)."""
        return crypto.derive_key(self._sign_sk, context)

    def open_sealed(self, blob: bytes) -> bytes:
        return crypto.open_sealed(self._box_sk, self._public.box_pk, blob)

    def __repr__(self) -> str:
        return f"Identity(device_id={self.device_id!r})"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("an Identity holds private keys and cannot be pickled")

    def _to_json(self) -> str:
        return json.dumps({"v": 1, "sign_sk": self._sign_sk.hex(), "sign_vk": self._public.sign_vk.hex(),
                           "box_sk": self._box_sk.hex(), "box_pk": self._public.box_pk.hex()})

    @classmethod
    def _from_json(cls, text: str) -> "Identity":
        try:
            d = json.loads(text)
            ident = cls(bytes.fromhex(d["sign_sk"]), bytes.fromhex(d["sign_vk"]), bytes.fromhex(d["box_sk"]), bytes.fromhex(d["box_pk"]))
            probe = b"council/identity/selfcheck"
            if not crypto.verify(ident._public.sign_vk, probe, ident.sign(probe)):  # the halves must belong together
                raise ValueError("signing keys do not match")
            if ident.open_sealed(crypto.seal(ident._public.box_pk, probe)) != probe:
                raise ValueError("box keys do not match")
            return ident
        except (ValueError, KeyError, TypeError, AttributeError, CryptoError) as exc:
            raise CryptoError("the key file is damaged") from exc


def _check_name(name: str) -> str:
    if not name or len(name) > _NAME_MAX or name in (".", "..") or not all(c.isalnum() or c in "-_." for c in name):
        raise ValueError("a key name is letters, digits, '-', '_' or '.', and at most 64 characters")
    return name


def _restrict_windows(path: Path) -> None:
    user = os.environ.get("USERNAME", "")
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"], check=True, capture_output=True)  # nosec B603, B607 - argument list, no shell, no user-controlled program; found on PATH on purpose


def _write_private(path: Path, data: bytes, exclusive: bool = True) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if os.name == "nt":
        _restrict_windows(path)
    else:
        os.chmod(path, 0o600)


class Keystore:
    """A directory of private key files, one per name."""

    def __init__(self, path) -> None:
        self.path = Path(path)

    def _file(self, name: str) -> Path:
        return self.path / f"{_check_name(name)}.key"

    def _ensure_dir(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.path, 0o700)

    def load_or_create(self, name: str) -> Identity:
        file = self._file(name)
        if file.exists():
            return Identity._from_json(file.read_text(encoding="utf-8"))
        self._ensure_dir()
        sign_sk, sign_vk = crypto.generate_signing_keypair()
        box_sk, box_pk = crypto.generate_box_keypair()
        ident = Identity(sign_sk, sign_vk, box_sk, box_pk)
        try:
            _write_private(file, ident._to_json().encode("utf-8"))
        except FileExistsError:  # another process created it first: use theirs
            return Identity._from_json(file.read_text(encoding="utf-8"))
        return ident

    def export(self, name: str, passphrase: str) -> bytes:
        """The identity as one encrypted blob for backup or moving to another machine (Argon2id + XSalsa20-Poly1305)."""
        file = self._file(name)
        if not file.exists():
            raise KeyError(f"no such key: {name}")
        ident = Identity._from_json(file.read_text(encoding="utf-8"))
        salt = crypto.random_bytes(argon2id.SALTBYTES)
        key = argon2id.kdf(nacl.secret.SecretBox.KEY_SIZE, passphrase.encode("utf-8"), salt, opslimit=KDF_OPS, memlimit=KDF_MEM)
        payload = json.dumps({"name": name, "identity": ident._to_json()}).encode("utf-8")
        return _EXPORT_MAGIC + struct.pack(">IQ", KDF_OPS, KDF_MEM) + salt + nacl.secret.SecretBox(key).encrypt(payload)

    def import_(self, blob: bytes, passphrase: str) -> str:
        """Restore an exported identity. Returns its name. Never overwrites an existing key."""
        head = len(_EXPORT_MAGIC) + 12 + argon2id.SALTBYTES
        try:
            if len(blob) < head or not blob.startswith(_EXPORT_MAGIC):
                raise CryptoError("this is not an exported key")
            ops, mem = struct.unpack(">IQ", blob[len(_EXPORT_MAGIC):len(_EXPORT_MAGIC) + 12])
            if not (argon2id.OPSLIMIT_MIN <= ops <= _MAX_KDF_OPS and argon2id.MEMLIMIT_MIN <= mem <= _MAX_KDF_MEM):
                raise CryptoError("the export asks for unreasonable key-stretching settings")
            salt = blob[len(_EXPORT_MAGIC) + 12:head]
            key = argon2id.kdf(nacl.secret.SecretBox.KEY_SIZE, passphrase.encode("utf-8"), salt, opslimit=ops, memlimit=mem)
            payload = json.loads(nacl.secret.SecretBox(key).decrypt(blob[head:]))
            name, ident = _check_name(payload["name"]), Identity._from_json(payload["identity"])
        except CryptoError:
            raise
        except (nacl.exceptions.CryptoError, nacl.exceptions.RuntimeError, ValueError, KeyError, TypeError) as exc:
            raise CryptoError("wrong passphrase, or the export is damaged") from exc
        file = self._file(name)
        self._ensure_dir()
        _write_private(file, ident._to_json().encode("utf-8"))  # exclusive: raises FileExistsError rather than overwrite
        return name


def safety_words(fingerprint: str) -> str:
    """Four words to read aloud when two people compare devices ("does yours say calm-otter-reef-lamp?"). Display only."""
    raw = bytes.fromhex(fingerprint)[:4]
    return "-".join(WORDS[b] for b in raw)
