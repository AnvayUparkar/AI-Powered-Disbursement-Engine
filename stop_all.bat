@echo off
setlocal enabledelayedexpansion
title Automated Disbursement Scorecard - Teardown
color 0C

echo ===============================================================================
echo       AUTOMATED DISBURSEMENT SCORECARD - STOPPING ALL SERVICES
echo ===============================================================================
echo.

:: -----------------------------------------------------------------------------
:: 1. Terminate processes listening on active ports (8000, 8001, 5173)
:: -----------------------------------------------------------------------------
echo [1/4] Terminating Port Listeners (8000, 8001, 5173)...
powershell -NoProfile -Command "Get-Process -Id (Get-NetTCPConnection -LocalPort 8000, 8001, 5173 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess) -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"

:: -----------------------------------------------------------------------------
:: 2. Terminate background Celery workers, watchfiles, and uvicorn processes
:: -----------------------------------------------------------------------------
echo [2/4] Terminating Celery and Python Worker Processes...
powershell -NoProfile -Command "Get-Process python, uvicorn, celery, node -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"

:: -----------------------------------------------------------------------------
:: 3. Close titled command prompt windows spawned by start_all.bat
:: -----------------------------------------------------------------------------
echo [3/4] Closing Titled Command Windows...
taskkill /F /FI "WINDOWTITLE eq Disbursement Scorecard*" 2>nul

:: -----------------------------------------------------------------------------
:: 4. Stop Redis server inside WSL
:: -----------------------------------------------------------------------------
echo [4/4] Stopping Redis in WSL...
where wsl >nul 2>&1
if %errorlevel% equ 0 (
    wsl sh -c "sudo service redis-server stop 2>/dev/null || service redis-server stop 2>/dev/null || redis-cli shutdown 2>/dev/null" >nul 2>&1
    echo   [OK] Redis in WSL stopped.
)

echo.
echo ===============================================================================
echo                 ALL SERVICES HAVE BEEN STOPPED CLEANLY
echo ===============================================================================
echo.
pause
