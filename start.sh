#!/usr/bin/env bash
# macOS / Linux launcher.
#   ./start.sh         -> production-like: builds the UI once (if missing) and serves UI + API on http://127.0.0.1:8000
#   ./start.sh --dev   -> development: API on :8000 and the Vite dev server (hot reload) on http://127.0.0.1:5173
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8000}"
open_browser() { (sleep 3; (xdg-open "$1" || open "$1") >/dev/null 2>&1 || true) & }

cd "$ROOT/backend" && pip install -q -r requirements.txt
if [ "$1" = "--dev" ]; then
  python3 -m uvicorn main:app --port 8000 &
  API=$!
  cd "$ROOT/frontend" && npm install --silent && npm run dev &
  UI=$!
  trap 'kill $API $UI 2>/dev/null' EXIT
  open_browser "http://127.0.0.1:5173"
  wait
else
  if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
    cd "$ROOT/frontend" && npm install --no-audit --no-fund && npm run build
  fi
  cd "$ROOT/backend"
  open_browser "http://127.0.0.1:$PORT"
  echo "ITC Shield: http://127.0.0.1:$PORT  (Ctrl+C stops)"
  exec python3 -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
fi
