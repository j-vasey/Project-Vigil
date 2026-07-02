@echo off
title Project Vigil Launcher
setlocal EnableDelayedExpansion

:: ============================================================
::  Project Vigil - launch.bat
::  Run this after git cloning the repository.
::  It sets up the environment on first run, then launches the
::  system-tray application.
:: ============================================================

set "ROOT=%~dp0"
set "VENV=%ROOT%venv"
set "WEBUI_DIST=%ROOT%webui\dist"

echo [Vigil] Checking Python...
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not on PATH.
    echo         Download from https://www.python.org/downloads/ and re-run.
    pause
    exit /b 1
)

:: Create virtual environment if missing
if not exist "%VENV%\Scripts\activate.bat" (
    echo [Vigil] Creating virtual environment...
    python -m venv "%VENV%"
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate and install dependencies
call "%VENV%\Scripts\activate.bat"

echo [Vigil] Installing / verifying Python dependencies...
pip install -q -r "%ROOT%requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] pip install failed. Check requirements.txt and your network connection.
    pause
    exit /b 1
)

:: Build WebUI if dist folder is missing or empty
if not exist "%WEBUI_DIST%\index.html" (
    echo [Vigil] Building WebUI frontend...
    where npm >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] npm not found. Install Node.js from https://nodejs.org/
        pause
        exit /b 1
    )
    pushd "%ROOT%webui"
    call npm install --silent
    call npm run build
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] WebUI build failed.
        popd
        pause
        exit /b 1
    )
    popd
)

echo [Vigil] Starting Project Vigil...
start "" pythonw "%ROOT%tray_app.py"
endlocal
