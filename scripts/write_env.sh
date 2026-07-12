#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
PAYROLL_ENV="${PAYROLL_ENV:-development}"
PF_DATABASE_URL="${PF_DATABASE_URL:-postgresql+asyncpg://pf_db:pf_db@localhost:5432/pf_db}"
PAYROLL_LOG_LEVEL="${PAYROLL_LOG_LEVEL:-INFO}"
PF_RATES_URL="${PF_RATES_URL:-http://localhost:8001}"

{
  printf 'PAYROLL_ENV=%s\n' "$PAYROLL_ENV"
  printf 'PF_DATABASE_URL=%s\n' "$PF_DATABASE_URL"
  printf 'PAYROLL_LOG_LEVEL=%s\n' "$PAYROLL_LOG_LEVEL"
  printf 'PF_RATES_URL=%s\n' "$PF_RATES_URL"
} > "$ENV_FILE"

printf '%s\n' "$ENV_FILE"
