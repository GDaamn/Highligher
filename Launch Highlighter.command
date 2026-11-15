#!/bin/zsh

# Finder launches .command files from an arbitrary working directory, so always
# anchor BookLamp to the folder containing this launcher.
set -u
unsetopt BG_NICE 2>/dev/null || true

BOOKLAMP_ROOT="${0:A:h}"
BOOKLAMP_HOST="127.0.0.1"
BOOKLAMP_PORT="${BOOKLAMP_PORT:-8010}"
BOOKLAMP_URL="http://${BOOKLAMP_HOST}:${BOOKLAMP_PORT}"

cd "$BOOKLAMP_ROOT" || exit 1

if [[ -f "$BOOKLAMP_ROOT/.env" ]]; then
  set -a
  source "$BOOKLAMP_ROOT/.env"
  set +a
fi

# The local heuristic keeps the app useful without an account or API key. A
# value set in .env still takes precedence for users who want DeepSeek focus.
export FOCUS_PROVIDER="${FOCUS_PROVIDER:-heuristic}"

print ""
print "  BookLamp"
print "  Opening at ${BOOKLAMP_URL}"
print ""

if curl --silent --fail --max-time 2 "${BOOKLAMP_URL}/api/health" 2>/dev/null | grep -q 'supported_extensions'; then
  print "BookLamp is already running. Opening the existing app..."
  if [[ "${BOOKLAMP_OPEN_BROWSER:-1}" != "0" ]]; then
    open "$BOOKLAMP_URL"
  fi
  exit 0
fi

if curl --silent --fail --max-time 2 "$BOOKLAMP_URL" >/dev/null 2>&1; then
  print "Port ${BOOKLAMP_PORT} is already being used by another app."
  print "Close that app, or launch BookLamp with a different BOOKLAMP_PORT."
  print ""
  read "?Press Return to close..."
  exit 1
fi

if [[ -x "$BOOKLAMP_ROOT/.venv/bin/python" ]] && "$BOOKLAMP_ROOT/.venv/bin/python" -c 'import uvicorn, replica' >/dev/null 2>&1; then
  BOOKLAMP_PYTHON="$BOOKLAMP_ROOT/.venv/bin/python"
else
  if command -v uv >/dev/null 2>&1; then
    BOOKLAMP_UV="$(command -v uv)"
  elif [[ -x "$HOME/.local/bin/uv" ]]; then
    BOOKLAMP_UV="$HOME/.local/bin/uv"
  elif [[ -x "/opt/homebrew/bin/uv" ]]; then
    BOOKLAMP_UV="/opt/homebrew/bin/uv"
  elif [[ -x "/usr/local/bin/uv" ]]; then
    BOOKLAMP_UV="/usr/local/bin/uv"
  else
    print "BookLamp needs the free 'uv' Python runner the first time it starts."
    print "Install it from https://docs.astral.sh/uv/ and double-click this file again."
    print ""
    read "?Press Return to close..."
    exit 1
  fi

  print "Preparing BookLamp for first use..."
  if ! "$BOOKLAMP_UV" sync; then
    print ""
    print "BookLamp could not finish installing its local components."
    read "?Press Return to close..."
    exit 1
  fi
  BOOKLAMP_PYTHON="$BOOKLAMP_ROOT/.venv/bin/python"
fi

shutdown_booklamp() {
  if [[ -n "${BOOKLAMP_SERVER_PID:-}" ]] && kill -0 "$BOOKLAMP_SERVER_PID" 2>/dev/null; then
    kill "$BOOKLAMP_SERVER_PID" 2>/dev/null
    wait "$BOOKLAMP_SERVER_PID" 2>/dev/null
  fi
}
trap shutdown_booklamp EXIT INT TERM

"$BOOKLAMP_PYTHON" -m uvicorn backend.app.main:app \
  --host "$BOOKLAMP_HOST" \
  --port "$BOOKLAMP_PORT" &
BOOKLAMP_SERVER_PID=$!

BOOKLAMP_READY=0
for _ in {1..120}; do
  if curl --silent --fail --max-time 1 "${BOOKLAMP_URL}/api/health" >/dev/null 2>&1; then
    BOOKLAMP_READY=1
    break
  fi
  if ! kill -0 "$BOOKLAMP_SERVER_PID" 2>/dev/null; then
    break
  fi
  sleep 0.25
done

if [[ "$BOOKLAMP_READY" == "1" ]]; then
  print ""
  print "BookLamp is ready. Keep this window open while you read."
  print "Press Control-C here when you want to stop BookLamp."
  print ""
  if [[ "${BOOKLAMP_OPEN_BROWSER:-1}" != "0" ]]; then
    open "$BOOKLAMP_URL"
  fi
else
  print ""
  print "BookLamp did not finish starting. Review the message above for details."
fi

wait "$BOOKLAMP_SERVER_PID"
BOOKLAMP_EXIT=$?
BOOKLAMP_SERVER_PID=""
exit "$BOOKLAMP_EXIT"
