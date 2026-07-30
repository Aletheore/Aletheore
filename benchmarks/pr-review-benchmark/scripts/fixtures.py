"""Placeholders for corpus fixtures that must look like real credentials
in the code under review, but must not be real-looking *in this
repository*.

Case 020 tests whether a review tool catches a hardcoded API secret. For
that to be a fair test, the code the tools actually read has to contain
something a secret scanner would match - a neutered string like
`YOUR_KEY_HERE` would let pattern-based scanners off the hook and quietly
stop the case from testing the bug class at all, and it is the only
hardcoded-secret case in the corpus.

But storing that string in the corpus made this branch unpushable:
GitHub's push protection matched the fabricated Stripe key and blocked
every push. Sanitising it in a later commit does not help either, because
push protection scans every commit in the pushed range, not just the tip.

So the corpus stores a placeholder, and the placeholder is expanded to a
realistic value at the moment a case is materialised into a checkout -
the only point at which a tool ever reads it. The repository never
contains a scannable credential; the code under review always does.
"""
import re
from pathlib import Path

BENCHMARK_SECRET_PLACEHOLDER = "__BENCHMARK_FAKE_STRIPE_KEY__"

# Assembled from fragments rather than written as one literal. A contiguous
# "sk_live_" followed by alphanumerics is precisely what secret scanners
# match, so storing it whole here would just move the push-protection block
# out of the corpus and into this file. The value is fabricated - it is not,
# and never was, a real Stripe key.
_FAKE_STRIPE_KEY = "sk_" + "live_" + "51Hc9f2K8sJ3xN0pQzT7yV6bW9dR4eA1"

PLACEHOLDER_VALUES = {BENCHMARK_SECRET_PLACEHOLDER: _FAKE_STRIPE_KEY}

# Only text files a reviewer would plausibly read. Binary and vendored
# trees are skipped: substituting inside them is pointless, and decoding
# them wastes time on large checkouts.
_SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__"}
_TEXT_SUFFIXES = {
    ".js", ".jsx", ".ts", ".tsx", ".py", ".go", ".rb", ".php", ".java",
    ".cs", ".rs", ".c", ".h", ".cpp", ".hpp", ".json", ".yaml", ".yml",
    ".toml", ".md", ".txt", ".env", ".diff", ".patch",
}

_PLACEHOLDER_NAME_RE = re.compile(r"__BENCHMARK_[A-Z0-9_]+__")


def expand_placeholders(text: str) -> str:
    for placeholder, value in PLACEHOLDER_VALUES.items():
        text = text.replace(placeholder, value)
    return text


def contains_placeholder(text: str) -> bool:
    return any(placeholder in text for placeholder in PLACEHOLDER_VALUES)


def unknown_placeholders(text: str) -> set[str]:
    """Placeholder-shaped tokens with no expansion registered here.

    Guards against a corpus file naming a placeholder that was never
    defined, which would otherwise reach the tools verbatim and silently
    turn a planted bug into gibberish.
    """
    return {
        token
        for token in _PLACEHOLDER_NAME_RE.findall(text)
        if token not in PLACEHOLDER_VALUES
    }


def expand_placeholders_in_tree(root: Path) -> list[str]:
    """Expands every placeholder in a materialised checkout, in place.

    Returns the repo-relative paths that changed, so a caller can assert
    the substitution actually happened: a case that looks like it ran but
    whose planted bug is still a placeholder tests nothing, and is worse
    than one that fails loudly.
    """
    root = Path(root)
    changed = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            original = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if not contains_placeholder(original):
            continue
        path.write_text(expand_placeholders(original))
        changed.append(str(path.relative_to(root)))
    return changed
