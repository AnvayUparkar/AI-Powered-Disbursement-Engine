#!/usr/bin/env bash
# Automated Disbursement Scorecard - Teardown (macOS / Linux)
# Usage: ./stop_all.sh [--quiet]

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/.logs/pids"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
say() { [ "$QUIET" = "1" ] || echo "$@"; }

say "==============================================================================="
say "      AUTOMATED DISBURSEMENT SCORECARD - STOPPING ALL SERVICES"
say "==============================================================================="

# 1. Processes launched by start_all.sh (children first, then the launcher)
say "[1/3] Terminating tracked service processes..."
if [ -f "$PID_FILE" ]; then
    while read -r pid; do
        [ -n "$pid" ] || continue
        pkill -TERM -P "$pid" 2>/dev/null
        kill -TERM "$pid" 2>/dev/null
    done < "$PID_FILE"
    : > "$PID_FILE"
fi

# 2. Anything still listening on the service ports
say "[2/3] Terminating port listeners (8000, 8001, 5173)..."
PORT_PIDS="$(lsof -ti tcp:8000,8001,5173 -sTCP:LISTEN 2>/dev/null)"
[ -n "$PORT_PIDS" ] && echo "$PORT_PIDS" | xargs kill -9 2>/dev/null

# Celery/watchfiles from this project only (avoid killing unrelated python/node)
pkill -f "$ROOT_DIR/venv/bin/python.*watchfiles" 2>/dev/null
pkill -f "celery -A pipeline.celery_app" 2>/dev/null

# 3. Redis (only when stopped explicitly; skipped on the launcher's cleanup pass)
if [ "$QUIET" = "0" ]; then
    say "[3/3] Stopping Redis..."
    redis-cli shutdown nosave 2>/dev/null
    say "  [OK] Redis stopped."
fi

say "==============================================================================="
say "                ALL SERVICES HAVE BEEN STOPPED CLEANLY"
say "==============================================================================="
exit 0
