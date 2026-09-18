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

:: -----------------------------------------------------------------------------
:: 2. Terminate background Celery workers, watchfiles, and uvicorn processes
:: -----------------------------------------------------------------------------
echo [2/4] Terminating Celery and Python Worker Processes...
powershell -NoProfile -Command ^
    "Get-CimInstance Win32_Process | Where-Object { " ^
    "    ($_.Name -match 'python' -and ($_.CommandLine -match 'celery' -or $_.CommandLine -match 'watchfiles' -or $_.CommandLine -match 'uvicorn')) " ^
    "} | ForEach-Object { " ^
    "    Write-Host ('  Stopping ' + $_.Name + ' (PID ' + $_.ProcessId + ')'); " ^
    "    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue " ^
    "}"

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
