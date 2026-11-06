# Campus Nexus — Launch Multi-Worker Daphne ASGI Cluster (PowerShell)
# Spawns 4 to 8 Daphne ASGI workers across CPU cores behind port 8000 proxy.

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $VenvPython = "python"
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  CAMPUS NEXUS — MULTI-WORKER ASGI CLUSTER LAUNCHER" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan

& $VenvPython "scripts\run_multiworker_cluster.py" --early-launch @args
