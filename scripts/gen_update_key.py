"""
Create the Ed25519 key pair that signs release SHA256SUMS files.

    python scripts/gen_update_key.py [--out PATH]

Writes the private key (PEM) and a base64 copy for the GitHub secret OUTSIDE
the repository, then prints the public key to paste into src/update_keys.py.
The private key is never printed. See docs/SIGNING.md.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

DEFAULT_OUT = Path.home() / ".claude-quota-tray-signing" / "update_signing_key.pem"


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def main(argv: list[str] | None = None) -> int:
    try:
        from Crypto.PublicKey import ECC
    except ImportError:
        print("ERROR: pycryptodome is required (pip install -r requirements.txt)",
              file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"private key path (default: {DEFAULT_OUT})")
    args = parser.parse_args(argv)

    pem_path: Path = args.out.expanduser().resolve()
    b64_path = pem_path.with_suffix(pem_path.suffix + ".b64")
    repo_root = Path(__file__).resolve().parent.parent
    if repo_root in pem_path.parents:
        print(f"ERROR: refusing to write the private key inside the repo ({pem_path})",
              file=sys.stderr)
        return 1
    for p in (pem_path, b64_path):
        if p.exists():
            print(f"ERROR: {p} already exists — not overwriting a signing key",
                  file=sys.stderr)
            return 1

    key = ECC.generate(curve="ed25519")
    pem = key.export_key(format="PEM").encode("ascii")
    public_hex = key.public_key().export_key(format="raw").hex()

    pem_path.parent.mkdir(parents=True, exist_ok=True)
    _write_private(pem_path, pem)
    _write_private(b64_path, base64.b64encode(pem) + b"\n")

    print("Ed25519 update-signing key created.\n")
    print(f"  private key (keep offline, never commit): {pem_path}")
    print(f"  GitHub secret value (base64 of the PEM):  {b64_path}\n")
    print("1. Store the secret (GitHub CLI):")
    print(f'     gh secret set UPDATE_SIGNING_KEY < "{b64_path}"')
    print("2. Add this public key to UPDATE_PUBLIC_KEYS in src/update_keys.py and commit it:")
    print(f'     "{public_hex}",')
    print("3. Back up the private key somewhere offline, then delete the .b64 copy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
