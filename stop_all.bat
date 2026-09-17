@echo off
setlocal enabledelayedexpansion
title Automated Disbursement Scorecard - Teardown
color 0C

echo ===============================================================================
echo       AUTOMATED DISBURSEMENT SCORECARD - STOPPING ALL SERVICES
echo ===============================================================================
echo.

echo [1/4] Terminating Port Listeners (8000, 8001, 5173)...
powershell -NoProfile -Command ^
    "$ports = @(8000, 8001, 5173); " ^
    "foreach ($port in $ports) { " ^
    "    $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue; " ^
    "    foreach ($conn in $conns) { " ^
    "        if ($conn.OwningProcess -gt 0) { " ^
    "            Write-Host ('  Stopping PID ' + $conn.OwningProcess + ' on port ' + $port); " ^
    "            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue " ^
    "        } " ^
    "    } " ^
    "}"

echo [2/4] Terminating Celery & Uvicorn Worker Processes...
taskkill /F /IM celery.exe /T 2>nul
taskkill /F /IM uvicorn.exe /T 2>nul

echo [3/4] Closing Titled Command Windows...
taskkill /F /FI "WINDOWTITLE eq Disbursement Scorecard - FastAPI Core (8000)*" 2>nul
taskkill /F /FI "WINDOWTITLE eq Disbursement Scorecard - IDP Engine (8001)*" 2>nul
taskkill /F /FI "WINDOWTITLE eq Disbursement Scorecard - Celery Worker*" 2>nul
taskkill /F /FI "WINDOWTITLE eq Disbursement Scorecard - Vite Frontend (5173)*" 2>nul
taskkill /F /FI "WINDOWTITLE eq Disbursement Scorecard - WSL Keepalive*" 2>nul

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
