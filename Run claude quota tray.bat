@echo off
REM Manual launcher. Runs the tray app silently (no console window).
REM Double-click this file to start the app without rebooting.

setlocal

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

set "VENV_PYW=%PROJECT_DIR%\.venv\Scripts\pythonw.exe"
set "MAIN_SCRIPT=%PROJECT_DIR%\src\main.py"

if not exist "%VENV_PYW%" (
    echo The app has not been installed yet.
    echo Please run "Setup claude quota tray.bat" first.
    echo.
    pause
    exit /b 1
)

if not exist "%MAIN_SCRIPT%" (
    echo Cannot find src\main.py
    echo Make sure this .bat file is in the project root.
    pause
    exit /b 1
)

start "" "%VENV_PYW%" "%MAIN_SCRIPT%"
endlocal
