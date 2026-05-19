@echo off
REM Build script for Windows. Run inside an activated virtualenv.
REM Produces dist\ClaudeQuotaTray.exe (single-file, no console window).

SET APP_NAME=ClaudeQuotaTray

echo === Cleaning previous build ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist %APP_NAME%.spec del %APP_NAME%.spec

echo === Building with PyInstaller ===
pyinstaller ^
    --onefile ^
    --windowed ^
    --name %APP_NAME% ^
    --paths src ^
    --hidden-import auth_discovery ^
    --hidden-import desktop_auth ^
    --collect-submodules Crypto ^
    src\main.py

if exist dist\%APP_NAME%.exe (
    echo.
    echo === BUILD OK ===
    echo Output: dist\%APP_NAME%.exe
    for %%I in (dist\%APP_NAME%.exe) do echo Size: %%~zI bytes
) else (
    echo === BUILD FAILED ===
    exit /b 1
)
