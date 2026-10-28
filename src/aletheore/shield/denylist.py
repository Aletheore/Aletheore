"""Paths Shield never reads, hashes or stores content for, because they usually hold secrets."""

import fnmatch
import posixpath
import re

_PATTERNS = (
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "*.jks",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "credentials*",
    "secrets.json",
    "secrets.yml",
    "secrets.yaml",
    ".npmrc",
    ".pypirc",
    ".netrc",
)


# One compiled alternation: the same fnmatch semantics, but cheap enough to run on every file in a checkpoint.
_DENIED = re.compile("|".join(f"(?:{fnmatch.translate(p)})" for p in _PATTERNS))


def is_denied_path(rel_path: str) -> bool:
    """True when the file name matches a secret-bearing pattern. Matching is on the base name, case-insensitive."""
    name = posixpath.basename(rel_path.replace("\\", "/")).lower()
    return _DENIED.match(name) is not None
