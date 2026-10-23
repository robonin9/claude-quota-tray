"""
Regenerate the hash-pinned lock files from the hand-edited requirements.

    python scripts/lock_requirements.py            # sync locks with requirements*.txt
    python scripts/lock_requirements.py --upgrade  # also move pins to the latest versions
    python scripts/lock_requirements.py --check    # CI: fail if a lock is stale

Uses ``uv pip compile --universal --generate-hashes`` so one lock covers
Windows and macOS (platform markers are kept) and every supported Python.
Existing pins are kept unless ``--upgrade`` is given, so ``--check`` fails
only when a lock no longer matches its requirements file — not every time a
dependency publishes a new release.
Installs then use ``pip install --require-hashes -r requirements.lock``.
Needs uv (``pip install uv==UV_VERSION``); pinned so CI and developers
produce byte-identical files.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UV_VERSION = "0.13.5"
PYTHON_MIN = "3.9"  # keep in sync with README "Python 3.9+"
ROOT = Path(__file__).resolve().parent.parent
LOCKS = [
    ("requirements.txt", "requirements.lock"),       # what users install
    ("requirements-dev.txt", "requirements-dev.lock"),  # + PyInstaller for builds
    ("requirements-sign.txt", "requirements-sign.lock"),  # CI signing job (Linux)
]


def _compile(src: Path, out: Path, *, upgrade: bool = False) -> None:
    """uv prefers the versions already pinned in ``out`` unless ``upgrade``."""
    subprocess.run(
        [
            sys.executable, "-m", "uv", "pip", "compile", str(src),
            "--universal", "--generate-hashes",
            "--python-version", PYTHON_MIN,
            "--output-file", str(out),
            "--custom-compile-command", "python scripts/lock_requirements.py",
            "--quiet",
            *(["--upgrade"] if upgrade else []),
        ],
        cwd=ROOT, check=True,
    )


def _normalized(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Regenerate requirements*.lock")
    p.add_argument("--check", action="store_true",
                   help="exit 1 if a lock file is out of date (nothing is written)")
    p.add_argument("--upgrade", action="store_true",
                   help="move every pin to the latest allowed version")
    p.add_argument("--uv-version", action="store_true",
                   help="print the pinned uv version (for CI) and exit")
    args = p.parse_args(argv)
    if args.uv_version:
        print(UV_VERSION)
        return 0
    if args.check and args.upgrade:
        p.error("--check and --upgrade are mutually exclusive")

    stale = []
    for src_name, lock_name in LOCKS:
        src, lock = ROOT / src_name, ROOT / lock_name
        if not args.check:
            _compile(src, lock, upgrade=args.upgrade)
            print(f"wrote {lock_name}")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / lock_name
            if lock.is_file():
                shutil.copyfile(lock, fresh)
            _compile(src, fresh)
            if not lock.is_file() or _normalized(lock) != _normalized(fresh):
                stale.append(lock_name)
    if stale:
        print(f"stale lock files: {', '.join(stale)} — run: python scripts/lock_requirements.py",
              file=sys.stderr)
        return 1
    if args.check:
        print("lock files are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
