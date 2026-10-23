#!/usr/bin/env bash
# First-time setup on macOS (run from repo root).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
source .venv/bin/activate
# requirements.lock pins exact versions + SHA-256 hashes; pip refuses mismatches.
# (No `pip install -U pip`: that download would not be hash-checked.)
pip install --require-hashes -r requirements.lock
echo "Done. Run: source .venv/bin/activate && python src/main.py"
