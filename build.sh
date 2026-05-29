#!/usr/bin/env bash
# Build script for macOS / Linux. Run inside an activated virtualenv.
set -euo pipefail

APP_NAME="ClaudeQuotaTray"

echo "=== Cleaning previous build ==="
rm -rf build dist "${APP_NAME}.spec"

echo "=== Building with PyInstaller ==="
pyinstaller \
    --onefile \
    --windowed \
    --name "$APP_NAME" \
    --paths src \
    --hidden-import auth_discovery \
    --hidden-import desktop_auth \
    --hidden-import updater \
    --hidden-import app_paths \
    --collect-submodules Crypto \
    src/main.py

if [ -f "dist/${APP_NAME}" ] || [ -d "dist/${APP_NAME}.app" ]; then
    echo
    echo "=== BUILD OK ==="
    ls -lh dist/
else
    echo "=== BUILD FAILED ==="
    exit 1
fi
