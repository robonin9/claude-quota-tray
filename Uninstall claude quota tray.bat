@echo off
setlocal
title Claude Quota Tray - Uninstall

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Claude Quota Tray.lnk"
set "VENV_DIR=%PROJECT_DIR%\.venv"
set "DATA_DIR=%USERPROFILE%\.claude-quota-tray"

echo.
echo ============================================================
echo   Claude Quota Tray  -  Uninstall
echo ============================================================
echo.

REM ---- Remove startup shortcut ----
if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    echo Removed startup shortcut.
) else (
    echo No startup shortcut found (already removed).
)
echo.

REM ---- Offer to remove the venv too ----
if exist "%VENV_DIR%" (
    choice /c YN /n /m "Also delete the virtual environment (.venv)? [Y/N] "
    echo.
    if not errorlevel 2 (
        echo Removing %VENV_DIR% ...
        rmdir /s /q "%VENV_DIR%"
        echo Done.
    )
) else (
    echo No virtual environment found.
)
echo.

REM ---- Offer to remove user data (settings, history, error log) ----
if exist "%DATA_DIR%" (
    echo Found user data: %DATA_DIR%
    echo This holds your saved accounts, settings, and usage history.
    choice /c YN /n /m "Also delete user data? [Y/N] "
    echo.
    if not errorlevel 2 (
        echo Removing %DATA_DIR% ...
        rmdir /s /q "%DATA_DIR%"
        echo Done.
    )
) else (
    echo No user data directory found.
)

echo.
echo Uninstall complete.
echo (You can also just delete this whole folder if you're done with it.)
echo.
pause
endlocal
