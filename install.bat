@echo off
title Project Vigil Installer
echo Starting Project Vigil Installer...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo Installation failed!
    pause
    exit /b %ERRORLEVEL%
)
echo Installation finished. Press any key to exit.
pause
