@echo off
setlocal
cd /d "%~dp0"
title Campus Nexus — Unified Server Orchestration
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Launcher exited with status %ERRORLEVEL%
    pause
)
