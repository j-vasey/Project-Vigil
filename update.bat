@echo off
title Project Vigil Updater
setlocal EnableDelayedExpansion

:: ============================================================
::  Project Vigil - update.bat
::  Pulls the latest code from GitHub and refreshes dependencies.
::  Your database and secrets in %APPDATA%\ProjectVigil\ are
::  NEVER touched by this script.
:: ============================================================

set "ROOT=%~dp0"
set "VENV=%ROOT%venv"

:: ---- 1. Git pull ----
echo [Vigil Update] Pulling latest changes from GitHub...
where git >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Git is not installed or not on PATH.
    echo         Download from https://git-scm.com/
    pause
    exit /b 1
)

pushd "%ROOT%."
git pull origin main
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] git pull failed. Check your internet connection and remote URL.
    popd
    pause
    exit /b 1
)
popd

:: ---- 2. Activate venv ----
if not exist "%VENV%\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Run launch.bat first to set it up.
    pause
    exit /b 1
)
call "%VENV%\Scripts\activate.bat"

:: ---- 3. Refresh Python dependencies ----
echo [Vigil Update] Refreshing Python dependencies...
pip install -q -r "%ROOT%requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

:: ---- 4. Rebuild WebUI ----
echo [Vigil Update] Rebuilding WebUI frontend...
where npm >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] npm not found - skipping WebUI rebuild.
    echo           Install Node.js from https://nodejs.org/ if you need it.
    goto done
)

if not exist "%ROOT%webui\package.json" (
    echo [WARNING] No webui\package.json found - skipping WebUI rebuild.
    goto done
)

pushd "%ROOT%webui"
call npm install --silent
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] npm install failed.
    popd
    pause
    exit /b 1
)
call npm run build
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] WebUI build failed.
    popd
    pause
    exit /b 1
)
popd

:done
echo.
echo [Vigil Update] Done! Run launch.bat to start Project Vigil.
pause
endlocal
