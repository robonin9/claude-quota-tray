"""
Build and sign the SHA256SUMS file for a release (used by release.yml).

    python scripts/sign_release.py sums   --version X.Y.Z --out SHA256SUMS FILE...
    python scripts/sign_release.py sign   --sums SHA256SUMS --out SHA256SUMS.sig
    python scripts/sign_release.py verify --sums SHA256SUMS --sig SHA256SUMS.sig

``sign`` reads the private key from the UPDATE_SIGNING_KEY environment
variable (the PEM, or base64 of it). ``verify`` checks the result with the
same code the app uses, against the keys in src/update_keys.py — so CI fails
if the secret doesn't match the committed public key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_sums(version: str, out: Path, files: list[Path]) -> int:
    if not _VERSION_RE.match(version):
        print(f"ERROR: version must be X.Y.Z, got {version!r}", file=sys.stderr)
        return 1
    names = [f.name for f in files]
    if len(set(names)) != len(names):
        print("ERROR: duplicate file names", file=sys.stderr)
        return 1
    lines = [f"version: {version}"]
    for f in files:
        if not f.is_file():
            print(f"ERROR: {f} not found", file=sys.stderr)
            return 1
        lines.append(f"{_sha256(f)}  {f.name}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(out.read_text(encoding="utf-8"), end="")
    return 0


def _load_private_key():
    from Crypto.PublicKey import ECC

    raw = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not raw:
        raise SystemExit("ERROR: UPDATE_SIGNING_KEY is not set (see docs/SIGNING.md)")
    pem = raw if raw.startswith("-----BEGIN") else base64.b64decode(raw).decode("ascii")
    key = ECC.import_key(pem)
    if key.curve != "Ed25519":
        raise SystemExit(f"ERROR: UPDATE_SIGNING_KEY is a {key.curve} key, not Ed25519")
    return key


def cmd_sign(sums: Path, out: Path) -> int:
    from Crypto.Signature import eddsa

    key = _load_private_key()
    signature = eddsa.new(key, "rfc8032").sign(sums.read_bytes())
    out.write_text(base64.b64encode(signature).decode("ascii") + "\n",
                   encoding="ascii", newline="\n")
    print(f"signed {sums.name} with key {key.public_key().export_key(format='raw').hex()}")
    return 0


def cmd_verify(sums: Path, sig: Path) -> int:
    import updater

    try:
        updater.verify_signature(sums.read_bytes(), sig.read_bytes())
        version, entries = updater.parse_sums(sums.read_bytes())
    except updater.UpdateVerificationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Is the public key for UPDATE_SIGNING_KEY listed in src/update_keys.py?",
              file=sys.stderr)
        return 1
    print(f"OK: version {version}, {len(entries)} files, signature matches src/update_keys.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build / sign / verify SHA256SUMS")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sums")
    s.add_argument("--version", required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("files", type=Path, nargs="+")
    g = sub.add_parser("sign")
    g.add_argument("--sums", type=Path, required=True)
    g.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--sums", type=Path, required=True)
    v.add_argument("--sig", type=Path, required=True)
    args = p.parse_args(argv)
    if args.cmd == "sums":
        return cmd_sums(args.version, args.out, args.files)
    if args.cmd == "sign":
        return cmd_sign(args.sums, args.out)
    return cmd_verify(args.sums, args.sig)


if __name__ == "__main__":
    raise SystemExit(main())
