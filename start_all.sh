#!/usr/bin/env bash
# Automated Disbursement Scorecard - Master Launcher (macOS / Linux)
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"

# Detect virtual environment (venv or .venv)
if [ -x "$ROOT_DIR/venv/bin/python" ]; then
    VENV_DIR="$ROOT_DIR/venv"
    PYTHON_EXE="$ROOT_DIR/venv/bin/python"
    CELERY_EXE="$ROOT_DIR/venv/bin/celery"
elif [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    VENV_DIR="$ROOT_DIR/.venv"
    PYTHON_EXE="$ROOT_DIR/.venv/bin/python"
    CELERY_EXE="$ROOT_DIR/.venv/bin/celery"
else
    VENV_DIR=""
    PYTHON_EXE=""
    CELERY_EXE=""
fi

LOG_DIR="$ROOT_DIR/.logs"
PID_FILE="$LOG_DIR/pids"

echo "==============================================================================="
echo "      AUTOMATED DISBURSEMENT SCORECARD - FULL SYSTEM LAUNCHER"
echo "==============================================================================="

# -----------------------------------------------------------------------------
# 1. Clean up stale instances
# -----------------------------------------------------------------------------
echo "[1/6] Cleaning up any previous running instances..."
"$ROOT_DIR/stop_all.sh" --quiet
mkdir -p "$LOG_DIR"
: > "$PID_FILE"
echo "  [OK] Clean state prepared."

# -----------------------------------------------------------------------------
# 2. Pre-flight dependency checks
# -----------------------------------------------------------------------------
echo
echo "[2/6] Checking environment dependencies..."

if [ -z "$PYTHON_EXE" ]; then
    echo "[ERROR] Python virtual environment not found at:"
    echo "        \"$ROOT_DIR/venv\" or \"$ROOT_DIR/.venv\""
    echo
    echo "Please create and install dependencies first:"
    echo "  python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi
echo "  [OK] Python venv found: \"$VENV_DIR\""

for bin in node npm; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "[ERROR] $bin is NOT installed or not in PATH (brew install node)."
        exit 1
    fi
    echo "  [OK] $bin found."
done

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo "  [WARN] frontend/node_modules missing. Installing npm packages now..."
    (cd "$FRONTEND_DIR" && npm install) || { echo "[ERROR] npm install failed."; exit 1; }
    echo "  [OK] Frontend dependencies installed."
else
    echo "  [OK] Frontend node_modules found."
fi

if [ ! -f "$ROOT_DIR/.env" ]; then
    if [ -f "$ROOT_DIR/.env.example" ]; then
        echo "  [WARN] .env not found. Creating from .env.example..."
        cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
        echo "  [OK] .env file created."
    else
        echo "  [WARN] Neither .env nor .env.example found. Default settings will be used."
    fi
else
    echo "  [OK] .env configuration file found."
fi

# Check offline model weights
if [ ! -d "$ROOT_DIR/models" ]; then
    echo "[ERROR] Model weights directory not found at:"
    echo "        \"$ROOT_DIR/models\""
    echo
    echo "In air-gapped environments, model weights must be pre-populated."
    echo "To download required weights on an internet-enabled system, run:"
    echo "  $PYTHON_EXE scripts/download_models.py"
    echo
    exit 1
else
    echo "  [OK] Local model weights directory found."
fi

# -----------------------------------------------------------------------------
# 3. Start Redis
# -----------------------------------------------------------------------------
echo
echo "[3/6] Checking Redis Server..."

if ! command -v redis-server >/dev/null 2>&1; then
    echo "[ERROR] Redis is NOT installed (brew install redis)."
    exit 1
fi

if ! redis-cli ping 2>/dev/null | grep -qi PONG; then
    redis-server --daemonize yes --protected-mode no >/dev/null 2>&1
fi

REDIS_READY=0
for _ in 1 2 3 4 5 6; do
    if redis-cli ping 2>/dev/null | grep -qi PONG; then REDIS_READY=1; break; fi
    sleep 1
done
if [ "$REDIS_READY" = "1" ]; then
    echo "  [OK] Redis server is running and accepting connections [PONG received]."
else
    echo "  [WARN] Redis ping timed out. Please verify Redis is installed and startable."
fi

# Launches a service in the background, logging to .logs/<name>.log and recording its PID.
launch() {
    local name="$1" dir="$2"; shift 2
    (cd "$dir" && exec "$@") >"$LOG_DIR/$name.log" 2>&1 &
    echo $! >> "$PID_FILE"
}

# -----------------------------------------------------------------------------
# 4. FastAPI Core Backend (8000)
# -----------------------------------------------------------------------------
echo
echo "[4/6] Launching FastAPI Core Backend (Port 8000)..."
launch fastapi "$ROOT_DIR" "$PYTHON_EXE" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-include '*.env'

# -----------------------------------------------------------------------------
# 5. IDP Engine Microservice (8001)
# -----------------------------------------------------------------------------
echo
echo "[5/6] Launching IDP Engine Microservice (Port 8001)..."
launch idp "$ROOT_DIR" "$PYTHON_EXE" -m uvicorn idp.main:app --host 0.0.0.0 --port 8001 --reload --reload-include '*.env'

echo "  Waiting for backend microservices to initialize..."
for _ in $(seq 1 30); do
    if curl -s http://127.0.0.1:8001/health 2>/dev/null | grep -qi "status"; then
        break
    fi
    sleep 1
done

# -----------------------------------------------------------------------------
# 6. Celery Worker (auto-reload), Flower Monitor, and Frontend UI
# -----------------------------------------------------------------------------
echo
echo "[6/6] Launching Celery Worker (with Auto-Reload), Flower, and Frontend UI..."
launch celery "$ROOT_DIR" "$PYTHON_EXE" -m watchfiles \
    "$PYTHON_EXE -m celery -A pipeline.celery_app worker -l info -P threads -E" \
    pipeline app config idp .env
launch flower "$ROOT_DIR" "$PYTHON_EXE" -m celery -A pipeline.celery_app flower --port=5555
launch frontend "$FRONTEND_DIR" npm run dev

echo
echo "==============================================================================="
echo "             ALL 6 SERVICES ARE RUNNING!"
echo "==============================================================================="
echo
echo "  [+] Frontend Web UI:          http://localhost:5173"
echo "  [+] FastAPI Core API:         http://localhost:8000 (Swagger: /docs)"
echo "  [+] IDP Engine Microservice:  http://localhost:8001 (Swagger: /docs)"
echo "  [+] Celery Background Worker: Active (threads pool)"
echo "  [+] Celery Flower Monitor:    http://localhost:5555"
echo "  [+] Redis Broker:             redis://127.0.0.1:6379/0"
echo
echo "  Logs:  $LOG_DIR/{fastapi,idp,celery,flower,frontend}.log"
echo "  Stop:  press Ctrl+C here (or run ./stop_all.sh from another terminal)"
echo "==============================================================================="
echo

# Stream all service logs to this terminal; Ctrl+C stops every service.
trap '"$ROOT_DIR/stop_all.sh"; exit 0' INT TERM
tail -n +1 -f "$LOG_DIR"/fastapi.log "$LOG_DIR"/idp.log "$LOG_DIR"/celery.log "$LOG_DIR"/flower.log "$LOG_DIR"/frontend.log
