import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


# The heading that opens the one section of an audit that is deliberately not
# evidence-backed (see scan_worker/managed_audit.py). It lives here rather
# than beside the writer because the *verifier* needs it too: a signature says
# "Aletheore produced this exact text", which is not the same as "every claim
# in it is backed by a citation". A certificate that doesn't distinguish the
# two lends the signature's authority to a model opinion.
#
# Treated as a contract: changing it changes what a verified report discloses,
# so both sides import this constant rather than spelling it out.
LLM_SUGGESTION_HEADING = "## LLM Based Suggestion (Not Evidence Backed)"


def contains_non_evidence_backed_section(report_text: str) -> bool:
    return LLM_SUGGESTION_HEADING in report_text


def content_hash(report_text: str) -> str:
    return hashlib.sha256(report_text.encode()).hexdigest()


def sign_report(report_text: str, private_key_hex: str) -> str:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return private_key.sign(content_hash(report_text).encode()).hex()


def verify_report(report_text: str, signature_hex: str, public_key_hex: str) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), content_hash(report_text).encode())
        return True
    except (InvalidSignature, ValueError):
        return False


def public_key_hex_from_private(private_key_hex: str) -> str:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return private_key.public_key().public_bytes_raw().hex()
