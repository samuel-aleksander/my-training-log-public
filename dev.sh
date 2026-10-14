#!/usr/bin/env bash
# Start the backend and the frontend together. Ctrl-C stops both.
set -euo pipefail

cd "$(dirname "$0")"

export BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# ------------------------------------------------------------------ dependencies

if [ ! -d backend/.venv ]; then
  echo "==> creating the backend virtualenv"
  python3 -m venv backend/.venv
fi

# Reinstall only when requirements.txt changed since the last run.
stamp=backend/.venv/.requirements.txt
if ! cmp -s backend/requirements.txt "$stamp"; then
  echo "==> installing backend dependencies"
  backend/.venv/bin/pip install --quiet --disable-pip-version-check -r backend/requirements.txt
  cp backend/requirements.txt "$stamp"
fi

if [ ! -d frontend/node_modules ]; then
  echo "==> installing frontend dependencies"
  (cd frontend && npm install)
fi

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Stop that process first:" >&2
    echo "  lsof -nP -iTCP:$port -sTCP:LISTEN" >&2
    exit 1
  fi
done

# ------------------------------------------------------------------ run

backend_pid=""
frontend_pid=""

cleanup() {
  trap - INT TERM EXIT
  [ -n "$frontend_pid" ] && kill "$frontend_pid" 2>/dev/null || true
  [ -n "$backend_pid" ] && kill "$backend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

(cd backend && exec .venv/bin/uvicorn app.main:app --reload --port "$BACKEND_PORT") &
backend_pid=$!

# The frontend proxies /api to the backend, so wait for it to answer before
# starting Vite. Otherwise the first requests fail with ECONNREFUSED.
printf "==> waiting for the backend"
ready=""
for _ in $(seq 60); do
  if ! kill -0 "$backend_pid" 2>/dev/null; then
    echo
    echo "The backend exited during startup." >&2
    exit 1
  fi
  if curl -sf "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  printf "."
  sleep 0.25
done
echo

if [ -z "$ready" ]; then
  echo "The backend did not answer on port $BACKEND_PORT." >&2
  exit 1
fi

(cd frontend && exec node_modules/.bin/vite --port "$FRONTEND_PORT" --strictPort) &
frontend_pid=$!

wait
