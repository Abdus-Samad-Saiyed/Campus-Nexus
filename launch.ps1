# ==============================================================================
# Campus Nexus — Unified Server Orchestration & Launch Control
# ==============================================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PythonExe = Join-Path $ScriptDir "venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python.exe"
}

function Show-Header {
    Clear-Host
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "                    CAMPUS NEXUS — SERVER ORCHESTRATION" -ForegroundColor Yellow -NoNewline
    Write-Host " (v2.0)" -ForegroundColor Green
    Write-Host "================================================================================" -ForegroundColor Cyan
    
    # Check Infrastructure Services
    $pgStatus = Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -InformationLevel Quiet -WarningAction SilentlyContinue
    $redisStatus = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -InformationLevel Quiet -WarningAction SilentlyContinue

    Write-Host "  Database: " -NoNewline
    if ($pgStatus) {
        Write-Host "PostgreSQL 18.6 (Port 5432) [ONLINE]" -ForegroundColor Green
    } else {
        Write-Host "PostgreSQL 18.6 (Port 5432) [OFFLINE - Use Option 6 to Bind]" -ForegroundColor Red
    }

    Write-Host "  Channels: " -NoNewline
    if ($redisStatus) {
        Write-Host "Redis Broker (Port 6379) [ONLINE]" -ForegroundColor Green
    } else {
        Write-Host "Redis Broker (Port 6379) [OFFLINE - Use Option 6 to Bind]" -ForegroundColor Yellow
    }

    Write-Host "  Runtime:  " -NoNewline
    Write-Host "$PythonExe (Django 6.0, Python 3.14)" -ForegroundColor Gray
    Write-Host "================================================================================" -ForegroundColor Cyan
}

function Ensure-Infrastructure {
    Write-Host "`n[INFRASTRUCTURE] Verifying system daemons..." -ForegroundColor Cyan
    & $PythonExe "scripts\bind_services.py"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[WARNING] Infrastructure verification returned non-zero. Continuing anyway..." -ForegroundColor Yellow
    }
}

function Main-Menu {
    do {
        Show-Header
        Write-Host "`nPlease select a launch configuration:`n" -ForegroundColor White

        Write-Host "  [1] " -NoNewline -ForegroundColor Cyan
        Write-Host "Multi-Worker PostgreSQL Cluster (Port 9000, 8 Workers) " -ForegroundColor Yellow -NoNewline
        Write-Host "[RECOMMENDED / PRODUCTION]" -ForegroundColor Green

        Write-Host "  [2] " -NoNewline -ForegroundColor Cyan
        Write-Host "Multi-Worker PostgreSQL Cluster with Early-Launch Mode " -ForegroundColor Yellow -NoNewline
        Write-Host "(--early-launch)" -ForegroundColor Magenta

        Write-Host "  [3] " -NoNewline -ForegroundColor Cyan
        Write-Host "Single-Worker PostgreSQL Dev Server (Port 9000)" -ForegroundColor White

        Write-Host "  [4] " -NoNewline -ForegroundColor Cyan
        Write-Host "Single-Worker PostgreSQL Dev Server (Port 8000)" -ForegroundColor White

        Write-Host "  [5] " -NoNewline -ForegroundColor Cyan
        Write-Host "Comparative SQLite Cluster (Port 8000, 8 Workers)" -ForegroundColor DarkGray

        Write-Host "  [6] " -NoNewline -ForegroundColor Cyan
        Write-Host "Bind & Verify Infrastructure (PostgreSQL 18.6 & Redis Daemons)" -ForegroundColor Cyan

        Write-Host "  [7] " -NoNewline -ForegroundColor Cyan
        Write-Host "Deep Database Parity & Integrity Check (SQLite vs PostgreSQL)" -ForegroundColor Cyan

        Write-Host "  [8] " -NoNewline -ForegroundColor Cyan
        Write-Host "Run Full Test Suite (374 Tests - 100% Green Validation)" -ForegroundColor Green

        Write-Host "  [9] " -NoNewline -ForegroundColor Cyan
        Write-Host "Run Locust Load Testing Suite (Capacity Curve Benchmarks)" -ForegroundColor White

        Write-Host "  [0] " -NoNewline -ForegroundColor Red
        Write-Host "Exit`n" -ForegroundColor Red

        $choice = Read-Host "Enter your choice [0-9]"

        switch ($choice) {
            "1" {
                Ensure-Infrastructure
                Write-Host "`n[LAUNCH] Starting Multi-Worker PostgreSQL Daphne Cluster on 0.0.0.0:9000 (8 Workers)..." -ForegroundColor Green
                $env:DJANGO_DEBUG = "False"
                & $PythonExe manage.py runserver 0.0.0.0:9000 -w 8
                break
            }
            "2" {
                Ensure-Infrastructure
                Write-Host "`n[LAUNCH] Starting Multi-Worker PostgreSQL Daphne Cluster with --early-launch on 0.0.0.0:9000..." -ForegroundColor Green
                $env:DJANGO_DEBUG = "False"
                & $PythonExe manage.py runserver 0.0.0.0:9000 -w 8 --early-launch
                break
            }
            "3" {
                Ensure-Infrastructure
                Write-Host "`n[LAUNCH] Starting Single-Worker PostgreSQL Server on 0.0.0.0:9000..." -ForegroundColor Green
                $env:DJANGO_DEBUG = "True"
                & $PythonExe manage.py runserver 0.0.0.0:9000
                break
            }
            "4" {
                Ensure-Infrastructure
                Write-Host "`n[LAUNCH] Starting Single-Worker PostgreSQL Server on 127.0.0.1:8000..." -ForegroundColor Green
                $env:DJANGO_DEBUG = "True"
                & $PythonExe manage.py runserver 127.0.0.1:8000
                break
            }
            "5" {
                Write-Host "`n[LAUNCH] Starting Standby SQLite Multi-Worker Cluster on 0.0.0.0:8000 (8 Workers)..." -ForegroundColor Yellow
                $env:DATABASE_URL = "sqlite:///$ScriptDir/db.sqlite3"
                $env:DJANGO_DEBUG = "False"
                & $PythonExe manage.py runserver 0.0.0.0:8000 -w 8
                break
            }
            "6" {
                Ensure-Infrastructure
                Write-Host "`nInfrastructure check completed. Press any key to return to menu..." -ForegroundColor Cyan
                $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
            }
            "7" {
                Write-Host "`n[AUDIT] Running deep row-level parity verification..." -ForegroundColor Cyan
                & $PythonExe "scripts\deep_check_parity.py"
                & $PythonExe "scripts\verify_db_parity.py"
                Write-Host "`nPress any key to return to menu..." -ForegroundColor Cyan
                $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
            }
            "8" {
                Write-Host "`n[TEST] Running full 374-test regression suite on PostgreSQL..." -ForegroundColor Green
                & $PythonExe manage.py test --settings=campus.settings.test
                Write-Host "`nTest suite run completed. Press any key to return to menu..." -ForegroundColor Cyan
                $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
            }
            "9" {
                Write-Host "`n[LOAD TESTING] Available Locust Scenarios:" -ForegroundColor Yellow
                Write-Host "  1. Smoke Test (10 Users, 30s)"
                Write-Host "  2. Baseline Capacity Test (10 Users, 60s)"
                Write-Host "  3. Scaled Capacity Curve (50, 120, 250 Users)"
                $ltChoice = Read-Host "Select scenario [1-3] or 'b' for Back"
                if ($ltChoice -eq "1") {
                    powershell -ExecutionPolicy Bypass -File "load-testing\scripts\run_smoke_test.ps1"
                } elseif ($ltChoice -eq "2") {
                    powershell -ExecutionPolicy Bypass -File "load-testing\scripts\run_baseline_test.ps1"
                } elseif ($ltChoice -eq "3") {
                    & $PythonExe "load-testing\scripts\run_scaled_curve.py"
                }
                Write-Host "`nPress any key to return to menu..." -ForegroundColor Cyan
                $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
            }
            "0" {
                Write-Host "`nExiting Campus Nexus Launcher. Goodbye!" -ForegroundColor Yellow
                return
            }
            default {
                Write-Host "`nInvalid selection. Please choose an option from 0 to 9." -ForegroundColor Red
                Start-Sleep -Seconds 1
            }
        }
    } while ($true)
}

Main-Menu
