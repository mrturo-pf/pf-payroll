#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
DB_NAME="${DB_NAME:-pf}"
DB_USER="${DB_USER:-pf}"
DB_PASSWORD="${DB_PASSWORD:-pf}"
DB_PORT="${DB_PORT:-5432}"
PAYROLL_ENV="${PAYROLL_ENV:-development}"
PAYROLL_LOG_LEVEL="${PAYROLL_LOG_LEVEL:-INFO}"
ADMINER_URL="${ADMINER_URL:-}"

DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@localhost:${DB_PORT}/${DB_NAME}"

{
  printf 'PAYROLL_ENV=%s\n' "$PAYROLL_ENV"
  printf 'PAYROLL_DATABASE_URL=%s\n' "$DATABASE_URL"
  printf 'PAYROLL_LOG_LEVEL=%s\n' "$PAYROLL_LOG_LEVEL"
  printf 'PAYROLL_DB_HOST=localhost\n'
  printf 'PAYROLL_DB_PORT=%s\n' "$DB_PORT"
  printf 'PAYROLL_DB_NAME=%s\n' "$DB_NAME"
  printf 'PAYROLL_DB_USER=%s\n' "$DB_USER"
  printf 'PAYROLL_DB_PASSWORD=%s\n' "$DB_PASSWORD"
  if [[ -n "$ADMINER_URL" ]]; then
    printf 'PAYROLL_ADMINER_URL=%s\n' "$ADMINER_URL"
  fi
} > "$ENV_FILE"

printf '%s\n' "$ENV_FILE"
