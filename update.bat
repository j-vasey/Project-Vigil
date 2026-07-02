@echo off
title Project Vigil Updater
setlocal EnableDelayedExpansion

:: ============================================================
::  Project Vigil - update.bat
::  Pulls the latest code from GitHub and refreshes dependencies.
::  Safe to run at any time: your database and secrets in
::  %APPDATA%\ProjectVigil\ are NEVER touched.
:: ============================================================

set "ROOT=%~dp0"
set "VENV=%ROOT%venv"
set "WEBUI_SRC=%ROOT%webui\src"
set "WEBUI_DIST=%ROOT%webui\dist"

echo [Vigil Update] Pulling latest changes from GitHub...
git -C "%ROOT%" pull origin main
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] git pull failed. Ensure you have Git installed and internet access.
    pause
    exit /b 1
)

:: Activate venv
if not exist "%VENV%\Scripts\activate.bat" (
    echo [Vigil Update] No virtual environment found - run launch.bat first.
    pause
    exit /b 1
)
call "%VENV%\Scripts\activate.bat"

echo [Vigil Update] Refreshing Python dependencies...
pip install -q -r "%ROOT%requirements.txt"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

:: Rebuild WebUI if source has changed more recently than the dist bundle
set "REBUILD_WEBUI=0"
if not exist "%WEBUI_DIST%\index.html" set "REBUILD_WEBUI=1"
:: Simple heuristic: if package.json is newer than index.html, rebuild
for /f %%A in ('powershell -NoProfile -Command "(Get-Item \"%WEBUI_SRC%\App.jsx\").LastWriteTime"') do set "SRC_DATE=%%A"
for /f %%A in ('powershell -NoProfile -Command "if (Test-Path \"%WEBUI_DIST%\index.html\") { (Get-Item \"%WEBUI_DIST%\index.html\").LastWriteTime } else { [datetime]::MinValue }"') do set "DIST_DATE=%%A"
powershell -NoProfile -Command "if ([datetime]\"%SRC_DATE%\" -gt [datetime]\"%DIST_DATE%\") { exit 1 } else { exit 0 }"
if %ERRORLEVEL% EQU 1 set "REBUILD_WEBUI=1"

if "%REBUILD_WEBUI%"=="1" (
    echo [Vigil Update] Rebuilding WebUI frontend...
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
) else (
    echo [Vigil Update] WebUI is up-to-date, skipping rebuild.
)

echo.
echo [Vigil Update] Update complete! Run launch.bat to start Project Vigil.
pause
endlocal
