#!/usr/bin/env bash
set -euo pipefail

NERDCTL_BIN="${NERDCTL_BIN:-nerdctl}"
DB_CONTAINER="${DB_CONTAINER:-pf-payroll-postgres}"
DB_VOLUME="${DB_VOLUME:-pf-payroll-postgres-data}"
DB_NAME="${DB_NAME:-payroll}"
DB_USER="${DB_USER:-payroll}"
DB_PASSWORD="${DB_PASSWORD:-payroll}"
DB_PORT="${DB_PORT:-5432}"
ADMINER_CONTAINER="${ADMINER_CONTAINER:-pf-payroll-adminer}"
ADMINER_PORT="${ADMINER_PORT:-8080}"
APP_PORT="${APP_PORT:-8000}"
VENV="${VENV:-.venv}"
ENV_FILE="${ENV_FILE:-.env}"

log() {
  printf '[local-up] %s\n' "$1"
}

venv_ready() {
  [[ -x "$VENV/bin/python" ]] && [[ -x "$VENV/bin/uvicorn" ]] && \
    "$VENV/bin/python" -c "import fastapi, greenlet, pydantic_settings, sqlalchemy, uvicorn" >/dev/null 2>&1
}

log "Starting or reusing PostgreSQL"
NERDCTL_BIN="$NERDCTL_BIN" \
DB_CONTAINER="$DB_CONTAINER" \
DB_VOLUME="$DB_VOLUME" \
DB_NAME="$DB_NAME" \
DB_USER="$DB_USER" \
DB_PASSWORD="$DB_PASSWORD" \
DB_PORT="$DB_PORT" \
./scripts/rancher_db.sh up

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
