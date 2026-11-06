@echo off
REM Campus Nexus — Launch Multi-Worker Daphne ASGI Cluster
REM Automatically utilizes available CPU cores behind asynchronous load-balancing proxy on port 8000.

cd /d "%~dp0\.."

REM Activate virtual environment if present
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo Starting Campus Nexus Multi-Worker ASGI Cluster...
python scripts\run_multiworker_cluster.py --early-launch %*

pause
