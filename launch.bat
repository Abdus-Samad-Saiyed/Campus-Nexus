@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Campus Nexus — Unified Server Orchestration

set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python.exe"
)

where powershell >nul 2>nul
if %ERRORLEVEL% neq 0 goto :run_python

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
if %ERRORLEVEL% equ 0 exit /b 0

echo.
echo [WARNING] PowerShell launcher returned code %ERRORLEVEL%. Falling back to Python launcher...

:run_python
"%PYTHON_EXE%" "%~dp0launch.py" %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Campus Nexus Launcher terminated with code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
exit /b 0
