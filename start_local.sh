#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AGENT_MODE="${AFTERHOURS_DEFAULT_AGENT:-auto}"
API_HOST="127.0.0.1"
API_PORT="${AFTERHOURS_API_PORT:-8765}"
FRONTEND_PORT="${AFTERHOURS_FRONTEND_PORT:-3000}"
PYTHON_BIN="${AFTERHOURS_PYTHON:-python3}"
FRONTEND_DIR="${AFTERHOURS_FRONTEND_DIR:-$ROOT_DIR/grid-system-copy}"
DB_PATH="${AFTERHOURS_DB:-$ROOT_DIR/microgrid_afterhours_hurdle_v1/microgrid.sqlite}"
BRAIN_API_URL="http://${API_HOST}:${API_PORT}"

usage() {
  echo "Usage: ./start_local.sh [--agent mock|gemini]"
  echo "  --agent mock     Use the deterministic offline agent by default"
  echo "  --agent gemini   Use Gemini by default (key from environment or dashboard)"
  echo "Without --agent, Automatic uses Gemini when GEMINI_API_KEY is set, otherwise Mock."
}

while (($#)); do
  case "$1" in
    --agent)
      if (($# < 2)); then
        echo "Missing value for --agent (choose mock or gemini)." >&2
        usage >&2
        exit 2
      fi
      AGENT_MODE="$2"
      shift 2
      ;;
    --agent=*)
      AGENT_MODE="${1#*=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$AGENT_MODE" != "auto" && "$AGENT_MODE" != "mock" && "$AGENT_MODE" != "gemini" ]]; then
  echo "Unsupported agent '$AGENT_MODE'. Choose mock, gemini, or auto." >&2
  exit 2
fi
export AFTERHOURS_DEFAULT_AGENT="$AGENT_MODE"

if [[ "$API_PORT" == "$FRONTEND_PORT" ]]; then
  echo "API and frontend cannot share port ${API_PORT}. Use distinct ports (defaults: API 8765, frontend 3000)." >&2
  exit 2
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 2
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required to serve the frontend." >&2
  exit 2
fi
if [[ ! -f "$DB_PATH" ]]; then
  echo "Microgrid database not found: ${DB_PATH}" >&2
  echo "Set AFTERHOURS_DB to an existing database file." >&2
  exit 2
fi
if [[ ! -f "$FRONTEND_DIR/server.js" ]]; then
  echo "Integrated frontend not found: ${FRONTEND_DIR}/server.js" >&2
  exit 2
fi

port_is_free() {
  "$PYTHON_BIN" -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind((sys.argv[1],int(sys.argv[2]))); s.close()' "$1" "$2" >/dev/null 2>&1
}

if ! port_is_free "$API_HOST" "$API_PORT"; then
  echo "API port ${API_PORT} is already in use. Stop its current process or set AFTERHOURS_API_PORT." >&2
  exit 2
fi
if ! port_is_free "$API_HOST" "$FRONTEND_PORT"; then
  echo "Frontend port ${FRONTEND_PORT} is already in use. Stop its current process or set AFTERHOURS_FRONTEND_PORT." >&2
  exit 2
fi

API_PID=""
FRONTEND_PID=""
cleanup() {
  local exit_status=$?
  trap - EXIT INT TERM
  [[ -z "$FRONTEND_PID" ]] || kill "$FRONTEND_PID" 2>/dev/null || true
  [[ -z "$API_PID" ]] || kill "$API_PID" 2>/dev/null || true
  [[ -z "$FRONTEND_PID" ]] || wait "$FRONTEND_PID" 2>/dev/null || true
  [[ -z "$API_PID" ]] || wait "$API_PID" 2>/dev/null || true
  exit "$exit_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$PYTHON_BIN" "$ROOT_DIR/main.py" serve \
  --db "$DB_PATH" --host "$API_HOST" --port "$API_PORT" &
API_PID=$!

(
  cd "$FRONTEND_DIR"
  export PORT="$FRONTEND_PORT"
  export BRAIN_API_URL
  exec node server.js
) &
FRONTEND_PID=$!

wait -n "$API_PID" "$FRONTEND_PID"
