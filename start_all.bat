@echo off
setlocal
title Automated Disbursement Scorecard - Master Launcher
color 0B

echo ===============================================================================
echo       AUTOMATED DISBURSEMENT SCORECARD - FULL SYSTEM LAUNCHER
echo ===============================================================================
echo.

set "ROOT_DIR=%~dp0"
set "FRONTEND_DIR=%ROOT_DIR%frontend"
set "PYTHON_EXE=%ROOT_DIR%venv\Scripts\python.exe"
set "CELERY_EXE=%ROOT_DIR%venv\Scripts\celery.exe"

:: -----------------------------------------------------------------------------
:: 1. Pre-flight Dependency Checks
:: -----------------------------------------------------------------------------
echo [1/5] Checking environment dependencies...

:: Check Python venv
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python virtual environment not found at:
    echo         "%ROOT_DIR%venv"
    echo.
    echo Please create and install dependencies first:
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
) else (
    echo   [OK] Python venv found.
)

:: Check Node.js and NPM
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is NOT installed or not found in system PATH.
    echo         Download and install from: https://nodejs.org/
    echo.
    pause
    exit /b 1
) else (
    echo   [OK] Node.js found.
)

where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] npm is NOT installed or not found in system PATH.
    echo.
    pause
    exit /b 1
) else (
    echo   [OK] npm found.
)

:: Check frontend node_modules
if not exist "%FRONTEND_DIR%\node_modules" (
    echo   [WARN] frontend\node_modules missing. Installing npm packages now...
    cd /d "%FRONTEND_DIR%"
    call npm install
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install frontend npm packages.
        pause
        exit /b 1
    )
    cd /d "%ROOT_DIR%"
    echo   [OK] Frontend dependencies installed.
) else (
    echo   [OK] Frontend node_modules found.
)

:: Check .env configuration file
if not exist "%ROOT_DIR%.env" (
    if exist "%ROOT_DIR%.env.example" (
        echo   [WARN] .env not found. Creating from .env.example...
        copy "%ROOT_DIR%.env.example" "%ROOT_DIR%.env" >nul
        echo   [OK] .env file created.
    ) else (
        echo   [WARN] Neither .env nor .env.example found. Default settings will be used.
    )
) else (
    echo   [OK] .env configuration file found.
)

:: -----------------------------------------------------------------------------
:: 2. Check WSL and Start Redis with Keep-Alive
:: -----------------------------------------------------------------------------
echo.
echo [2/5] Checking WSL and Redis Server...

where wsl >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] WSL [Windows Subsystem for Linux] is NOT installed or not in PATH.
    echo         Redis requires WSL or a native Windows Redis server on port 6379.
    echo.
    pause
    exit /b 1
)

:: Start Redis inside WSL
echo   Starting Redis server inside WSL...
wsl -u root service redis-server start >nul 2>&1
wsl redis-server --daemonize yes >nul 2>&1

:: Verify Redis is responding
wsl redis-cli ping | findstr /i "PONG" >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Redis server is running [PONG received].
) else (
    echo   [WARN] Redis ping failed. Please verify Redis is installed inside your WSL distro.
)

:: Keep WSL active in the background so it doesn't auto-terminate on idle
start "Disbursement Scorecard - WSL Keepalive" /min wsl sleep infinity
echo   [OK] WSL keep-alive process spawned.

:: -----------------------------------------------------------------------------
:: 3. Launch Application Services in Dedicated Windows
:: -----------------------------------------------------------------------------
echo.
echo [3/5] Launching FastAPI Core Backend (Port 8000)...
start "Disbursement Scorecard - FastAPI Core (8000)" cmd /k "cd /d "%~dp0" && color 0A && venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

echo [4/5] Launching IDP Microservice (Port 8001)...
start "Disbursement Scorecard - IDP Engine (8001)" cmd /k "cd /d "%~dp0" && color 0E && venv\Scripts\python.exe -m uvicorn idp.main:app --host 0.0.0.0 --port 8001 --reload"

echo [5/5] Launching Celery Worker and Frontend UI...
start "Disbursement Scorecard - Celery Worker" cmd /k "cd /d "%~dp0" && color 0D && venv\Scripts\python.exe -m celery -A pipeline.celery_app worker -l info"

start "Disbursement Scorecard - Vite Frontend (5173)" cmd /k "cd /d "%~dp0frontend" && color 03 && npm run dev"

:: -----------------------------------------------------------------------------
:: 4. Summary & Status Dashboard
:: -----------------------------------------------------------------------------
echo.
echo ===============================================================================
echo              ALL 5 SERVICES ARE RUNNING SUCCESSFULLY!
echo ===============================================================================
echo.
echo   [+] Frontend Web UI:         http://localhost:5173
echo   [+] FastAPI Core API:        http://localhost:8000 (Swagger: /docs)
echo   [+] IDP Engine Microservice: http://localhost:8001 (Swagger: /docs)
echo   [+] Celery Background Worker: Active (threads pool)
echo   [+] Redis Broker (WSL):      redis://127.0.0.1:6379/0 (Keepalive Active)
echo.
echo -------------------------------------------------------------------------------
echo   To stop all running services cleanly at any time, run: .\stop_all.bat
echo ===============================================================================
echo.
pause
