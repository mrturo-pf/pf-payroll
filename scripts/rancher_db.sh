#!/usr/bin/env bash
# Database is now managed by pf-db (shared with pf-rates).
# This script only supports checking readiness and opening a psql session.
# To start/stop/reset the database use the pf-db repository:
#   cd ../pf-db && make db-up          # start
#   cd ../pf-db && make db-down        # stop
#   cd ../pf-db && make db-reset       # destroy and recreate
#   cd ../pf-db && make seed-test      # load test fixtures
set -euo pipefail

ACTION="${1:-psql}"
DB_CONTAINER="${DB_CONTAINER:-pf-db-db-1}"
DB_NAME="${DB_NAME:-pf}"
DB_USER="${DB_USER:-pf}"

log() {
  printf '[rancher-db] %s\n' "$1"
}

container_is_running() {
  docker inspect --format '{{.State.Status}}' "$DB_CONTAINER" 2>/dev/null | grep -q "^running$"
}

wait_for_postgres() {
  log "Waiting for PostgreSQL to accept connections in $DB_CONTAINER"
  for _ in $(seq 1 30); do
    if docker exec "$DB_CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "PostgreSQL did not become ready in time." >&2
  exit 1
}

open_psql() {
  exec docker exec -it "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME"
}

case "$ACTION" in
  psql)
    if ! container_is_running; then
      echo ""
      echo "ERROR: pf-db container '$DB_CONTAINER' is not running."
      echo "Start it with: cd ../pf-db && make db-up"
      echo ""
      exit 1
    fi
    wait_for_postgres
    open_psql
    ;;
  up|reset-data|down)
    echo ""
    echo "INFO: pf-payroll no longer manages its own database container."
    echo "Use the pf-db repository instead:"
    echo "  up        → cd ../pf-db && make db-up"
    echo "  reset     → cd ../pf-db && make db-reset"
    echo "  down      → cd ../pf-db && make db-down"
    echo "  seed-test → cd ../pf-db && make seed-test"
    echo ""
    ;;
  *)
    echo "Usage: $0 {up|reset-data|down|psql}" >&2
    exit 1
    ;;
esac
