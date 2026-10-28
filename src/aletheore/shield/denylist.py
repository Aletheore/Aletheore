"""Paths Shield never reads, hashes or stores content for, because they usually hold secrets."""

import fnmatch
import posixpath

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


def is_denied_path(rel_path: str) -> bool:
    """True when the file name matches a secret-bearing pattern. Matching is on the base name, case-insensitive."""
    name = posixpath.basename(rel_path.replace("\\", "/")).lower()
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in _PATTERNS)
