#!/usr/bin/env bash
set -euo pipefail

# Database is now managed by pf-db (shared with pf-rates).
# The pf-db container must be running before this script is called.
# Start it with: cd ../pf-db && make db-up

DB_CONTAINER="${DB_CONTAINER:-pf-db-db-1}"
DB_NAME="${DB_NAME:-pf}"
DB_USER="${DB_USER:-pf}"
DB_PASSWORD="${DB_PASSWORD:-pf}"
DB_PORT="${DB_PORT:-5432}"
ADMINER_CONTAINER="${ADMINER_CONTAINER:-pf-payroll-adminer}"
ADMINER_PORT="${ADMINER_PORT:-8080}"
APP_PORT="${APP_PORT:-8000}"
VENV="${VENV:-.venv}"
ENV_FILE="${ENV_FILE:-.env}"
NERDCTL_BIN="${NERDCTL_BIN:-nerdctl}"

log() {
  printf '[local-up] %s\n' "$1"
}

venv_ready() {
  [[ -x "$VENV/bin/python" ]] && [[ -x "$VENV/bin/uvicorn" ]] && \
    "$VENV/bin/python" -c "import fastapi, greenlet, multipart, pydantic_settings, sqlalchemy, uvicorn" >/dev/null 2>&1
}

# Verify the shared pf-db container is running.
log "Checking shared pf-db container ($DB_CONTAINER)"
if ! docker inspect --format '{{.State.Status}}' "$DB_CONTAINER" 2>/dev/null | grep -q "^running$"; then
  echo ""
  echo "ERROR: pf-db container '$DB_CONTAINER' is not running."
  echo ""
  echo "Start the shared database first:"
  echo "  cd ../pf-db && make db-up"
  echo ""
  exit 1
fi
log "pf-db container is running"

log "Starting or reusing Adminer"
adminer_output="$(
  NERDCTL_BIN="$NERDCTL_BIN" \
  ADMINER_CONTAINER="$ADMINER_CONTAINER" \
  ADMINER_PORT="$ADMINER_PORT" \
  ./scripts/adminer.sh up
)"
printf '%s\n' "$adminer_output"
adminer_url="$(printf '%s\n' "$adminer_output" | tail -n 1)"

log "Writing environment file to $ENV_FILE"
ADMINER_URL="$adminer_url" \
DB_NAME="$DB_NAME" \
DB_USER="$DB_USER" \
DB_PASSWORD="$DB_PASSWORD" \
DB_PORT="$DB_PORT" \
ENV_FILE="$ENV_FILE" \
./scripts/write_env.sh >/dev/null

if venv_ready; then
  log "Reusing existing virtual environment in $VENV"
else
  log "Installing project dependencies"
  if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
  fi
  "$VENV/bin/python" -m ensurepip --upgrade
  "$VENV/bin/python" -m pip install -e ".[dev]"
fi

printf '\n'
printf 'Adminer: %s\n' "$adminer_url"
printf 'API: http://127.0.0.1:%s\n' "$APP_PORT"
printf 'Env file: %s\n' "$ENV_FILE"
printf '\n'

exec "$VENV/bin/uvicorn" payroll.interfaces.api.main:app --reload --host 127.0.0.1 --port "$APP_PORT"
