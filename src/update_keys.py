"""
Ed25519 public keys trusted to sign release SHA256SUMS files.

Each entry is the 32-byte raw public key as 64 hex characters, printed by
``python scripts/gen_update_key.py``. Updates are accepted when the signature
matches any key here; to rotate, append the new key, ship a release that
trusts both, then remove the old one (see docs/SIGNING.md).

An empty list means no release can be verified, so in-app updates are
refused. A fork must put its own key here — it cannot install releases signed
by someone else's key, and vice versa.
"""

UPDATE_PUBLIC_KEYS: list[str] = [
    # robonin9 release key, created 2026-10-23
    "14753277de50ca714b1cd72dbd1ff787be9b32b38cb231b4b2011682e64abd63",
]
