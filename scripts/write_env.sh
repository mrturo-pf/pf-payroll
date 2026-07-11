#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
PAYROLL_ENV="${PAYROLL_ENV:-development}"
PAYROLL_DATABASE_URL="${PAYROLL_DATABASE_URL:-postgresql+asyncpg://pf_db:pf_db@localhost:5432/pf_db}"
PAYROLL_LOG_LEVEL="${PAYROLL_LOG_LEVEL:-INFO}"

{
  printf 'PAYROLL_ENV=%s\n' "$PAYROLL_ENV"
  printf 'PAYROLL_DATABASE_URL=%s\n' "$PAYROLL_DATABASE_URL"
  printf 'PAYROLL_LOG_LEVEL=%s\n' "$PAYROLL_LOG_LEVEL"
} > "$ENV_FILE"

printf '%s\n' "$ENV_FILE"
