"""Paths Shield never reads, hashes or stores content for, because they usually hold secrets."""

import fnmatch
import posixpath
import re

# Matched against the lower-cased base name only, so a folder name never matters and "\" (Windows) and "/" both
# separate folders.
_PATTERNS = (
    ".env*",
    "*.env",  # prod.env, staging.env: environment files named for their environment
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.ppk",
    "*.keystore",
    "*.jks",
    "*.tfvars",
    "*.tfvars.json",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "credentials*",
    ".git-credentials",
    "secrets.json",
    "secrets.yml",
    "secrets.yaml",
    ".npmrc",
    ".pypirc",
    ".netrc",
)
# "<name>.env.<environment>" (app.env.local, api.env.production) is an environment file too. Only environment and
# backup names count: values.env.yaml, config.env.go or app.env.example are configuration, source or a template.
_ENV_NAMES = (
    "local", "production", "prod", "staging", "stage", "dev", "development", "test", "testing", "qa", "uat", "ci",
    "preview", "secret", "secrets", "backup", "bak", "old", "orig", "save",
)
_ENV_VARIANT = r".+\.env\.(?:" + "|".join(_ENV_NAMES) + r")"


# One compiled alternation: the same fnmatch semantics, but cheap enough to run on every file in a checkpoint.
_DENIED = re.compile("|".join([*(f"(?:{fnmatch.translate(p)})" for p in _PATTERNS), f"(?:{_ENV_VARIANT}\\Z)"]))


def is_denied_path(rel_path: str) -> bool:
    """True when the file name matches a secret-bearing pattern. Matching is on the base name, case-insensitive, with
    either folder separator."""
    name = posixpath.basename(rel_path.replace("\\", "/")).lower()
    return _DENIED.match(name) is not None
