#!/usr/bin/env bash
# Build script for macOS / Linux. Run inside an activated virtualenv.
set -euo pipefail

APP_NAME="ClaudeQuotaTray"

echo "=== Cleaning previous build ==="
rm -rf build dist "${APP_NAME}.spec"

if [ ! -f "assets/app.ico" ]; then
    echo "[WARN] assets/app.ico not found — run: python scripts/generate_app_icon.py"
fi

echo "=== Building with PyInstaller ==="
ICON_ARGS=()
if [ -f "assets/app.ico" ]; then
    ICON_ARGS=(--icon assets/app.ico)
fi
pyinstaller \
    --onefile \
    --windowed \
    --name "$APP_NAME" \
    "${ICON_ARGS[@]}" \
    --paths src \
    --hidden-import auth_discovery \
    --hidden-import desktop_auth \
    --hidden-import updater \
    --hidden-import app_paths \
    --hidden-import chart_widget \
    --hidden-import desktop_widget \
    --hidden-import ui_theme \
    --hidden-import app_platform \
    --hidden-import platform_win \
    --hidden-import platform_darwin \
    --hidden-import token_store \
    --hidden-import ui_host \
    --hidden-import tray_icon \
    --hidden-import self_test \
    --hidden-import update_keys \
    --collect-submodules Crypto \
    src/main.py

# Menu-bar-only app: no Dock icon (also set at runtime, see hide_dock_icon()).
PLIST="dist/${APP_NAME}.app/Contents/Info.plist"
if [ -f "$PLIST" ]; then
    /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"
fi

if [ -f "dist/${APP_NAME}" ] || [ -d "dist/${APP_NAME}.app" ]; then
    echo
    echo "=== BUILD OK ==="
    ls -lh dist/
else
    echo "=== BUILD FAILED ==="
    exit 1
fi
