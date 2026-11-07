# ==============================================================================
# Campus Nexus - Unified Server Orchestration & Launch Control (v2.1)
# ==============================================================================
param(
    [Alias("w")][int]$Workers = 0,
    [Alias("p")][int]$Port = 0,
    [Alias("b")][string]$Bind = "",
    [string]$PgHost = "",
    [int]$PgPort = 0,
    [string]$RedisHost = "",
    [int]$RedisPort = 0,
    [switch]$Debug,
    [switch]$EarlyLaunch,
    [switch]$CheckServices
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PythonExe = Join-Path $ScriptDir "venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python.exe"
}

$ProfilesFile = Join-Path $ScriptDir "scripts\launch_profiles.json"

function Get-ActiveProfile {
    if (Test-Path $ProfilesFile) {
        try {
            $data = Get-Content $ProfilesFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $activeName = $data.active_profile
            if ($data.profiles.$activeName) {
                return @{
                    Key = $activeName
                    Data = $data.profiles.$activeName
                    All = $data
                }
            }
        } catch { }
    }
    # Fallback default
    return @{
        Key = "production"
        Data = [PSCustomObject]@{
            name = "Production Multi-Worker Cluster (Recommended)"
            daphne_host = "0.0.0.0"
            daphne_port = 8000
            daphne_workers = 8
            worker_base_port = 8001
            pg_host = "127.0.0.1"
            pg_port = 5432
            pg_db = "campus_nexus"
            pg_user = "campus_admin"
            pg_password = "CampusPass123!"
            redis_host = "127.0.0.1"
            redis_port = 6379
            redis_db = 0
            debug = $false
            early_launch = $false
        }
        All = $null
    }
}

function Save-ActiveProfile([string]$profileKey, [PSCustomObject]$profileObj) {
    try {
        $data = $null
        if (Test-Path $ProfilesFile) {
            $data = Get-Content $ProfilesFile -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        if (-not $data) {
            $data = [PSCustomObject]@{
                active_profile = $profileKey
                profiles = [PSCustomObject]@{}
            }
        }
        $data.active_profile = $profileKey
        if (-not $data.profiles) {
            $data | Add-Member -MemberType NoteProperty -Name "profiles" -Value ([PSCustomObject]@{})
        }
        if ($data.profiles.PSObject.Properties[$profileKey]) {
            $data.profiles.$profileKey = $profileObj
        } else {
            $data.profiles | Add-Member -MemberType NoteProperty -Name $profileKey -Value $profileObj
        }
        $json = $data | ConvertTo-Json -Depth 5
        Set-Content -Path $ProfilesFile -Value $json -Encoding UTF8
        return $true
    } catch {
        Write-Host "`n[WARNING] Could not save profile: $_" -ForegroundColor Yellow
        return $false
    }
}

function Show-Header {
    Clear-Host
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "                    CAMPUS NEXUS - SERVER ORCHESTRATION" -ForegroundColor Yellow -NoNewline
    Write-Host " (v2.1)" -ForegroundColor Green
    Write-Host "================================================================================" -ForegroundColor Cyan
    
    $prof = Get-ActiveProfile
    $p = $prof.Data

    # Probe Infrastructure Sockets
    $pgStatus = Test-NetConnection -ComputerName $p.pg_host -Port ([int]$p.pg_port) -InformationLevel Quiet -WarningAction SilentlyContinue
    $redisStatus = Test-NetConnection -ComputerName $p.redis_host -Port ([int]$p.redis_port) -InformationLevel Quiet -WarningAction SilentlyContinue
    $cfStatus = (Get-Process cloudflared -ErrorAction SilentlyContinue) -ne $null

    Write-Host "  Profile:   " -NoNewline
    Write-Host "$($p.name) [$($prof.Key)]" -ForegroundColor White

    Write-Host "  Daphne:    " -NoNewline
    Write-Host "$($p.daphne_host):$($p.daphne_port) " -ForegroundColor Yellow -NoNewline
    Write-Host "($($p.daphne_workers) Workers on ports $($p.worker_base_port)..$([int]$p.worker_base_port + [int]$p.daphne_workers - 1))" -ForegroundColor Cyan

    Write-Host "  Database:  " -NoNewline
    Write-Host "PostgreSQL $($p.pg_host):$($p.pg_port)/$($p.pg_db) " -NoNewline
    if ($pgStatus) {
        Write-Host "[ONLINE]" -ForegroundColor Green
    } else {
        Write-Host "[OFFLINE - Socket Unreachable]" -ForegroundColor Red
    }

    Write-Host "  Channels:  " -NoNewline
    Write-Host "Redis Broker $($p.redis_host):$($p.redis_port)/$($p.redis_db) " -NoNewline
    if ($redisStatus) {
        Write-Host "[ONLINE]" -ForegroundColor Green
    } else {
        Write-Host "[OFFLINE - Socket Unreachable]" -ForegroundColor Yellow
    }

    Write-Host "  Tunnel:    " -NoNewline
    Write-Host "Cloudflare Anycast Tunnel (dev.campus-nexus.online) " -NoNewline
    if ($cfStatus) {
        Write-Host "[ONLINE]" -ForegroundColor Green
    } else {
        Write-Host "[OFFLINE - Will auto-start on launch]" -ForegroundColor Yellow
    }

    Write-Host "  Runtime:   " -NoNewline
    Write-Host "$PythonExe (Django 6.0, Python 3.14)" -ForegroundColor Gray
    Write-Host "================================================================================" -ForegroundColor Cyan
}

function Ensure-Infrastructure($pgHost="127.0.0.1", $pgPort=5432, $redisHost="127.0.0.1", $redisPort=6379, $daphnePort=8000) {
    Write-Host "`n[INFRASTRUCTURE] Verifying system daemons (PostgreSQL, Redis, Cloudflared) on $pgHost`:$pgPort, $redisHost`:$redisPort, Port $daphnePort..." -ForegroundColor Cyan
    & $PythonExe "scripts\bind_services.py" --pg-host $pgHost --pg-port ([int]$pgPort) --redis-host $redisHost --redis-port ([int]$redisPort) --daphne-port ([int]$daphnePort)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[WARNING] Infrastructure verification returned non-zero. Continuing anyway..." -ForegroundColor Yellow
    }
}

function Invoke-CampusServer([scriptblock]$cmdBlock, $pgHost="127.0.0.1", $pgPort=5432, $redisHost="127.0.0.1", $redisPort=6379, $daphnePort=8000) {
    try {
        Ensure-Infrastructure -pgHost $pgHost -pgPort $pgPort -redisHost $redisHost -redisPort $redisPort -daphnePort $daphnePort
        & $cmdBlock
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            Write-Host "`n[ERROR] Server process terminated with exit code $exitCode." -ForegroundColor Red
            Write-Host "Press any key to return to menu..." -ForegroundColor Cyan
            try { $null = [System.Console]::ReadKey($true) } catch { $null = Read-Host }
        } else {
            Write-Host "`n[INFO] Server stopped. Press any key to return to menu..." -ForegroundColor Cyan
            try { $null = [System.Console]::ReadKey($true) } catch { $null = Read-Host }
        }
    } catch {
        Write-Host "`n[FATAL ERROR] An error occurred: $_" -ForegroundColor Red
        Write-Host "Press any key to return to menu..." -ForegroundColor Cyan
        try { $null = [System.Console]::ReadKey($true) } catch { $null = Read-Host }
    }
}

function Configure-CustomDeployment {
    Clear-Host
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "         CAMPUS NEXUS  -  CUSTOM DEPLOYMENT SOCKET & WORKER CONFIGURATOR" -ForegroundColor Yellow
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "Configure deployment sockets (IP:Port), worker processes, and database settings.`n" -ForegroundColor Gray

    $active = (Get-ActiveProfile).Data

    # --- 1. Daphne Web / WebSocket Socket ---
    Write-Host "--- [1] DAPHNE ASGI / HTTP / WEBSOCKET DEPLOYMENT SOCKET ---" -ForegroundColor Green
    $inHost = Read-Host "  Public Bind Host [default: $($active.daphne_host)]"
    $daphneHost = if ([string]::IsNullOrWhiteSpace($inHost)) { $active.daphne_host } else { $inHost }

    $inPort = Read-Host "  Public Proxy Port [default: $($active.daphne_port)]"
    $daphnePort = if ([string]::IsNullOrWhiteSpace($inPort)) { $active.daphne_port } else { [int]$inPort }

    $cpuCores = [Environment]::ProcessorCount
    $inWorkers = Read-Host "  Daphne Worker Count (System has $cpuCores threads) [default: $($active.daphne_workers)]"
    $daphneWorkers = if ([string]::IsNullOrWhiteSpace($inWorkers)) { $active.daphne_workers } else { [int]$inWorkers }

    $inBase = Read-Host "  Worker Internal Base Port [default: $($active.worker_base_port)]"
    $basePort = if ([string]::IsNullOrWhiteSpace($inBase)) { $active.worker_base_port } else { [int]$inBase }

    # --- 2. PostgreSQL Deployment Socket ---
    Write-Host "`n--- [2] POSTGRESQL DATABASE DEPLOYMENT SOCKET ---" -ForegroundColor Green
    $inPgH = Read-Host "  PostgreSQL Host [default: $($active.pg_host)]"
    $pgHost = if ([string]::IsNullOrWhiteSpace($inPgH)) { $active.pg_host } else { $inPgH }

    $inPgP = Read-Host "  PostgreSQL Port [default: $($active.pg_port)]"
    $pgPort = if ([string]::IsNullOrWhiteSpace($inPgP)) { $active.pg_port } else { [int]$inPgP }

    $inPgDb = Read-Host "  Database Name [default: $($active.pg_db)]"
    $pgDb = if ([string]::IsNullOrWhiteSpace($inPgDb)) { $active.pg_db } else { $inPgDb }

    $inPgU = Read-Host "  Database Username [default: $($active.pg_user)]"
    $pgUser = if ([string]::IsNullOrWhiteSpace($inPgU)) { $active.pg_user } else { $inPgU }

    $inPgPass = Read-Host "  Database Password [default: $($active.pg_password)]"
    $pgPass = if ([string]::IsNullOrWhiteSpace($inPgPass)) { $active.pg_password } else { $inPgPass }

    # --- 3. Redis Channel Layer Socket ---
    Write-Host "`n--- [3] REDIS CHANNEL BROKER SOCKET ---" -ForegroundColor Green
    $inRHost = Read-Host "  Redis Host [default: $($active.redis_host)]"
    $redisHost = if ([string]::IsNullOrWhiteSpace($inRHost)) { $active.redis_host } else { $inRHost }

    $inRPort = Read-Host "  Redis Port [default: $($active.redis_port)]"
    $redisPort = if ([string]::IsNullOrWhiteSpace($inRPort)) { $active.redis_port } else { [int]$inRPort }

    $inRDb = Read-Host "  Redis DB Index [default: $($active.redis_db)]"
    $redisDb = if ([string]::IsNullOrWhiteSpace($inRDb)) { $active.redis_db } else { [int]$inRDb }

    # --- 4. Runtime Mode Flags ---
    Write-Host "`n--- [4] RUNTIME FLAGS ---" -ForegroundColor Green
    $inDebug = Read-Host "  Enable Debug Traceable Mode? (y/N) [default: N]"
    $isDebug = ($inDebug -match "^[yY]")

    $inEarly = Read-Host "  Enable Early Launch Mode (--early-launch)? (y/N) [default: N]"
    $isEarly = ($inEarly -match "^[yY]")

    # Probe sockets live
    Write-Host "`n[PROBING] Testing connection to configured sockets..." -ForegroundColor Cyan
    $testPg = Test-NetConnection -ComputerName $pgHost -Port ([int]$pgPort) -InformationLevel Quiet -WarningAction SilentlyContinue
    $testRedis = Test-NetConnection -ComputerName $redisHost -Port ([int]$redisPort) -InformationLevel Quiet -WarningAction SilentlyContinue

    Write-Host "  * PostgreSQL Socket ($pgHost`:$pgPort): " -NoNewline
    if ($testPg) { Write-Host "CONNECTED [OK]" -ForegroundColor Green } else { Write-Host "OFFLINE / UNREACHABLE [WARN]" -ForegroundColor Yellow }

    Write-Host "  * Redis Broker Socket ($redisHost`:$redisPort): " -NoNewline
    if ($testRedis) { Write-Host "CONNECTED [OK]" -ForegroundColor Green } else { Write-Host "OFFLINE / UNREACHABLE [WARN]" -ForegroundColor Yellow }

    Write-Host "  * Daphne Deployment Socket: $daphneHost`:$daphnePort ($daphneWorkers Workers on ports $basePort..$($basePort + $daphneWorkers - 1))`n" -ForegroundColor Cyan

    Write-Host "What would you like to do?" -ForegroundColor White
    Write-Host "  [L] Launch Cluster with these parameters" -ForegroundColor Green
    Write-Host "  [S] Save Profile and Launch" -ForegroundColor Yellow
    Write-Host "  [B] Return to Main Menu" -ForegroundColor DarkGray

    $action = Read-Host "`nEnter selection [L/S/B]"
    if ($action -match "^[bB]") { return }

    $profileObj = [PSCustomObject]@{
        name = "Custom Deployment ($daphneHost`:$daphnePort, $daphneWorkers Workers)"
        daphne_host = $daphneHost
        daphne_port = $daphnePort
        daphne_workers = $daphneWorkers
        worker_base_port = $basePort
        pg_host = $pgHost
        pg_port = $pgPort
        pg_db = $pgDb
        pg_user = $pgUser
        pg_password = $pgPass
        redis_host = $redisHost
        redis_port = $redisPort
        redis_db = $redisDb
        debug = $isDebug
        early_launch = $isEarly
    }

    if ($action -match "^[sS]") {
        $pName = Read-Host "Enter profile name to save as [default: custom]"
        if ([string]::IsNullOrWhiteSpace($pName)) { $pName = "custom" }
        Save-ActiveProfile $pName $profileObj | Out-Null
        Write-Host "Profile '$pName' saved as active configuration!" -ForegroundColor Green
    }

    # Execute custom cluster deployment
    $env:DATABASE_URL = "postgres://$pgUser`:$pgPass@$pgHost`:$pgPort/$pgDb"
    $env:REDIS_URL = "redis://$redisHost`:$redisPort/$redisDb`?protocol=2&socket_timeout=15&socket_connect_timeout=5"
    $env:DJANGO_DEBUG = if ($isDebug) { "True" } else { "False" }

    Write-Host "`n[LAUNCH] Starting $daphneWorkers-Worker Daphne Cluster on $daphneHost`:$daphnePort..." -ForegroundColor Green
    Invoke-CampusServer -pgHost $pgHost -pgPort $pgPort -redisHost $redisHost -redisPort $redisPort -daphnePort $daphnePort {
        if ($isEarly) {
            & $PythonExe manage.py runserver "$daphneHost`:$daphnePort" -w ([int]$daphneWorkers) --early-launch
        } else {
            & $PythonExe manage.py runserver "$daphneHost`:$daphnePort" -w ([int]$daphneWorkers)
        }
    }
}

function Switch-DeploymentProfile {
    Clear-Host
    Write-Host "================================================================================" -ForegroundColor Cyan
    Write-Host "              CAMPUS NEXUS  -  SWITCH DEPLOYMENT PROFILE" -ForegroundColor Yellow
    Write-Host "================================================================================" -ForegroundColor Cyan

    if (-not (Test-Path $ProfilesFile)) {
        Write-Host "`nNo saved profiles file found." -ForegroundColor Yellow
        Start-Sleep -Seconds 1
        return
    }

    $data = Get-Content $ProfilesFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $props = $data.profiles.PSObject.Properties | Select-Object -ExpandProperty Name

    Write-Host "`nAvailable deployment profiles:`n" -ForegroundColor White
    $idx = 1
    $map = @{}
    foreach ($p in $props) {
        $activeMarker = if ($p -eq $data.active_profile) { " (ACTIVE)" } else { "" }
        Write-Host "  [$idx] $p$activeMarker" -ForegroundColor Yellow -NoNewline
        Write-Host "  -  $($data.profiles.$p.name)" -ForegroundColor Gray
        $map[$idx.ToString()] = $p
        $idx++
    }

    Write-Host "`n  [B] Back to Main Menu`n" -ForegroundColor DarkGray
    $sel = Read-Host "Select profile number to activate"
    if ($map.ContainsKey($sel)) {
        $chosen = $map[$sel]
        $data.active_profile = $chosen
        $json = $data | ConvertTo-Json -Depth 5
        Set-Content -Path $ProfilesFile -Value $json -Encoding UTF8
        Write-Host "`nActivated profile '$chosen'!" -ForegroundColor Green
        Start-Sleep -Seconds 1
    }
}

function Main-Menu {
    do {
        Show-Header
        $active = (Get-ActiveProfile).Data
        Write-Host "`nSelect a launch configuration or configurator:`n" -ForegroundColor White

        Write-Host "  [1] " -NoNewline -ForegroundColor Cyan
        Write-Host "Active Profile Cluster ($($active.daphne_host):$($active.daphne_port), $($active.daphne_workers) Workers) " -ForegroundColor Yellow -NoNewline
        Write-Host "[RECOMMENDED / CURRENT PROFILE]" -ForegroundColor Green

        Write-Host "  [2] " -NoNewline -ForegroundColor Cyan
        Write-Host "Active Profile Cluster with Early-Launch Mode " -ForegroundColor Yellow -NoNewline
        Write-Host "(--early-launch)" -ForegroundColor Magenta

        Write-Host "  [3] " -NoNewline -ForegroundColor Cyan
        Write-Host "Single-Worker Dev Server ($($active.daphne_host):$($active.daphne_port), Debug=True)" -ForegroundColor White

        Write-Host "  [4] " -NoNewline -ForegroundColor Cyan
        Write-Host "Turbo High-Concurrency Cluster (16 Workers on 0.0.0.0:8000)" -ForegroundColor Green

        Write-Host "  [5] " -NoNewline -ForegroundColor Cyan
        Write-Host "Comparative SQLite Multi-Worker Cluster (Port 9000, 8 Workers)" -ForegroundColor DarkGray

        Write-Host "`n  --- CUSTOM SOCKETS & PROFILE MANAGEMENT ---" -ForegroundColor DarkCyan

        Write-Host "  [C] " -NoNewline -ForegroundColor Yellow
        Write-Host "Custom Deployment & Socket Configurator " -ForegroundColor Yellow -NoNewline
        Write-Host "(Select IP/Port sockets & worker counts for Daphne, PG, Redis, Tunnel)" -ForegroundColor Gray

        Write-Host "  [P] " -NoNewline -ForegroundColor Yellow
        Write-Host "Switch / Manage Deployment Profiles " -ForegroundColor Yellow -NoNewline
        Write-Host "(Production, Turbo, Dev, Staging, Custom)" -ForegroundColor Gray

        Write-Host "`n  --- INFRASTRUCTURE & VERIFICATION ---" -ForegroundColor DarkCyan

        Write-Host "  [6] " -NoNewline -ForegroundColor Cyan
        Write-Host "Bind & Verify Infrastructure (PostgreSQL, Redis & Cloudflared Daemons)" -ForegroundColor Cyan

        Write-Host "  [7] " -NoNewline -ForegroundColor Cyan
        Write-Host "Deep Database Parity & Integrity Check (SQLite vs PostgreSQL)" -ForegroundColor Cyan

        Write-Host "  [8] " -NoNewline -ForegroundColor Cyan
        Write-Host "Run Full Test Suite (100% Green Validation)" -ForegroundColor Green

        Write-Host "  [9] " -NoNewline -ForegroundColor Cyan
        Write-Host "Run Locust Load Testing Suite (Capacity Curve Benchmarks)" -ForegroundColor White

        Write-Host "  [0] " -NoNewline -ForegroundColor Red
        Write-Host "Exit`n" -ForegroundColor Red

        $choice = Read-Host "Enter your choice [1-9, C, P, 0]"

        switch -Regex ($choice) {
            "^[1]$" {
                $p = (Get-ActiveProfile).Data
                $env:DATABASE_URL = "postgres://$($p.pg_user):$($p.pg_password)@$($p.pg_host):$($p.pg_port)/$($p.pg_db)"
                $env:REDIS_URL = "redis://$($p.redis_host):$($p.redis_port)/$($p.redis_db)?protocol=2&socket_timeout=15&socket_connect_timeout=5"
                $env:DJANGO_DEBUG = if ($p.debug) { "True" } else { "False" }

                Write-Host "`n[LAUNCH] Starting $($p.daphne_workers)-Worker Daphne Cluster on $($p.daphne_host):$($p.daphne_port)..." -ForegroundColor Green
                Invoke-CampusServer -pgHost $p.pg_host -pgPort $p.pg_port -redisHost $p.redis_host -redisPort $p.redis_port -daphnePort $p.daphne_port {
                    if ($p.early_launch) {
                        & $PythonExe manage.py runserver "$($p.daphne_host):$($p.daphne_port)" -w ([int]$p.daphne_workers) --early-launch
                    } else {
                        & $PythonExe manage.py runserver "$($p.daphne_host):$($p.daphne_port)" -w ([int]$p.daphne_workers)
                    }
                }
            }
            "^[2]$" {
                $p = (Get-ActiveProfile).Data
                $env:DATABASE_URL = "postgres://$($p.pg_user):$($p.pg_password)@$($p.pg_host):$($p.pg_port)/$($p.pg_db)"
                $env:REDIS_URL = "redis://$($p.redis_host):$($p.redis_port)/$($p.redis_db)?protocol=2&socket_timeout=15&socket_connect_timeout=5"
                $env:DJANGO_DEBUG = "False"

                Write-Host "`n[LAUNCH] Starting $($p.daphne_workers)-Worker Cluster with --early-launch on $($p.daphne_host):$($p.daphne_port)..." -ForegroundColor Green
                Invoke-CampusServer -pgHost $p.pg_host -pgPort $p.pg_port -redisHost $p.redis_host -redisPort $p.redis_port -daphnePort $p.daphne_port {
                    & $PythonExe manage.py runserver "$($p.daphne_host):$($p.daphne_port)" -w ([int]$p.daphne_workers) --early-launch
                }
            }
            "^[3]$" {
                $p = (Get-ActiveProfile).Data
                $env:DATABASE_URL = "postgres://$($p.pg_user):$($p.pg_password)@$($p.pg_host):$($p.pg_port)/$($p.pg_db)"
                $env:REDIS_URL = "redis://$($p.redis_host):$($p.redis_port)/$($p.redis_db)?protocol=2&socket_timeout=15&socket_connect_timeout=5"
                $env:DJANGO_DEBUG = "True"

                Write-Host "`n[LAUNCH] Starting Single-Worker PostgreSQL Server on $($p.daphne_host):$($p.daphne_port)..." -ForegroundColor Green
                Invoke-CampusServer -pgHost $p.pg_host -pgPort $p.pg_port -redisHost $p.redis_host -redisPort $p.redis_port -daphnePort $p.daphne_port {
                    & $PythonExe manage.py runserver "$($p.daphne_host):$($p.daphne_port)"
                }
            }
            "^[4]$" {
                Write-Host "`n[LAUNCH] Starting Turbo High-Concurrency Daphne Cluster (16 Workers) on 0.0.0.0:8000..." -ForegroundColor Green
                $env:DJANGO_DEBUG = "False"
                Invoke-CampusServer { & $PythonExe manage.py runserver 0.0.0.0:8000 -w 16 }
            }
            "^[5]$" {
                Write-Host "`n[LAUNCH] Starting Comparative SQLite Multi-Worker Cluster on 0.0.0.0:9000 (8 Workers)..." -ForegroundColor Yellow
                $env:DATABASE_URL = "sqlite:///$ScriptDir/db.sqlite3"
                $env:DJANGO_DEBUG = "False"
                Invoke-CampusServer { & $PythonExe manage.py runserver 0.0.0.0:9000 -w 8 }
            }
            "^[cC]$" {
                Configure-CustomDeployment
            }
            "^[pP]$" {
                Switch-DeploymentProfile
            }
            "^[6]$" {
                $p = (Get-ActiveProfile).Data
                Ensure-Infrastructure -pgHost $p.pg_host -pgPort $p.pg_port -redisHost $p.redis_host -redisPort $p.redis_port -daphnePort $p.daphne_port
                Write-Host "`nInfrastructure check completed. Press any key to return to menu..." -ForegroundColor Cyan
                try { $null = [System.Console]::ReadKey($true) } catch { $null = Read-Host }
            }
            "^[7]$" {
                Write-Host "`n[AUDIT] Running deep row-level parity verification..." -ForegroundColor Cyan
                & $PythonExe "scripts\deep_check_parity.py"
                & $PythonExe "scripts\verify_db_parity.py"
                Write-Host "`nPress any key to return to menu..." -ForegroundColor Cyan
                try { $null = [System.Console]::ReadKey($true) } catch { $null = Read-Host }
            }
            "^[8]$" {
                Write-Host "`n[TEST] Running full regression test suite on PostgreSQL..." -ForegroundColor Green
                & $PythonExe manage.py test --settings=campus.settings.test
                Write-Host "`nTest suite run completed. Press any key to return to menu..." -ForegroundColor Cyan
                try { $null = [System.Console]::ReadKey($true) } catch { $null = Read-Host }
            }
            "^[9]$" {
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
                try { $null = [System.Console]::ReadKey($true) } catch { $null = Read-Host }
            }
            "^[0]$" {
                Write-Host "`nExiting Campus Nexus Launcher. Goodbye!" -ForegroundColor Yellow
                return
            }
            default {
                Write-Host "`nInvalid selection. Please choose an option from the menu." -ForegroundColor Red
                Start-Sleep -Seconds 1
            }
        }
    } while ($true)
}

if ($CheckServices) {
    $p = (Get-ActiveProfile).Data
    $h = if ($PgHost) { $PgHost } else { $p.pg_host }
    $pp = if ($PgPort -gt 0) { $PgPort } else { $p.pg_port }
    $rh = if ($RedisHost) { $RedisHost } else { $p.redis_host }
    $rp = if ($RedisPort -gt 0) { $RedisPort } else { $p.redis_port }
    $dp = if ($Port -gt 0) { $Port } else { $p.daphne_port }
    Ensure-Infrastructure -pgHost $h -pgPort $pp -redisHost $rh -redisPort $rp -daphnePort $dp
    exit 0
}

if ($Workers -gt 0 -or $Port -gt 0 -or $Bind -ne "") {
    $p = (Get-ActiveProfile).Data
    $hostAddr = if ($Bind) { $Bind } else { $p.daphne_host }
    $portNum = if ($Port -gt 0) { $Port } else { $p.daphne_port }
    $wCount = if ($Workers -gt 0) { $Workers } else { $p.daphne_workers }
    $h = if ($PgHost) { $PgHost } else { $p.pg_host }
    $pp = if ($PgPort -gt 0) { $PgPort } else { $p.pg_port }
    $rh = if ($RedisHost) { $RedisHost } else { $p.redis_host }
    $rp = if ($RedisPort -gt 0) { $RedisPort } else { $p.redis_port }

    $env:DATABASE_URL = "postgres://$($p.pg_user):$($p.pg_password)@$($h):$($pp)/$($p.pg_db)"
    $env:REDIS_URL = "redis://$($rh):$($rp)/$($p.redis_db)?protocol=2&socket_timeout=15&socket_connect_timeout=5"
    $env:DJANGO_DEBUG = if ($Debug) { "True" } else { "False" }

    Write-Host "`n[HEADLESS LAUNCH] Starting $wCount-Worker Daphne Cluster on $hostAddr`:$portNum..." -ForegroundColor Green
    $extraArgs = @()
    if ($EarlyLaunch) { $extraArgs += "--early-launch" }
    Invoke-CampusServer -pgHost $h -pgPort $pp -redisHost $rh -redisPort $rp -daphnePort $portNum {
        & $PythonExe manage.py runserver "$hostAddr`:$portNum" -w $wCount $extraArgs
    }
    exit 0
}

Main-Menu
