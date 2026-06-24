#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-up}"
NERDCTL_BIN="${NERDCTL_BIN:-nerdctl}"
CLI_BIN=""
DB_CONTAINER="${DB_CONTAINER:-pf-payroll-postgres}"
DB_VOLUME="${DB_VOLUME:-pf-payroll-postgres-data}"
DB_NAME="${DB_NAME:-payroll}"
DB_USER="${DB_USER:-payroll}"
DB_PASSWORD="${DB_PASSWORD:-payroll}"
DB_PORT="${DB_PORT:-5432}"
SCHEMA_FILE="${SCHEMA_FILE:-db/01_schema.sql}"
BASE_SEED_FILE="${BASE_SEED_FILE:-db/02_seed_base.sql}"
TEST_SEED_FILE="${TEST_SEED_FILE:-db/03_seed_test.sql}"
REAL_SEED_FILE="${REAL_SEED_FILE:-db/03_seed_real.sql}"
APPLY_TEST_SEED="${APPLY_TEST_SEED:-0}"
APPLY_REAL_SEED="${APPLY_REAL_SEED:-0}"

log() {
  printf '[rancher-db] %s\n' "$1"
}

# shellcheck source=scripts/shared/container_runtime.sh
source "$(dirname "$0")/shared/container_runtime.sh"

container_exists() {
  "$CLI_BIN" container inspect "$DB_CONTAINER" >/dev/null 2>&1
}

container_is_running() {
  [[ "$("$CLI_BIN" inspect --format '{{.State.Status}}' "$DB_CONTAINER" 2>/dev/null)" == "running" ]]
}

ensure_volume() {
  if ! "$CLI_BIN" volume inspect "$DB_VOLUME" >/dev/null 2>&1; then
    log "Creating volume $DB_VOLUME"
    "$CLI_BIN" volume create "$DB_VOLUME" >/dev/null
  fi
}

create_container() {
  log "Creating PostgreSQL container $DB_CONTAINER"
  "$CLI_BIN" run -d \
    --name "$DB_CONTAINER" \
    --restart unless-stopped \
    -e POSTGRES_DB="$DB_NAME" \
    -e POSTGRES_USER="$DB_USER" \
    -e POSTGRES_PASSWORD="$DB_PASSWORD" \
    -p "$DB_PORT:5432" \
    -v "$DB_VOLUME:/var/lib/postgresql/data" \
    --health-cmd='pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    --health-interval=5s \
    --health-timeout=5s \
    --health-retries=20 \
    postgres:16 >/dev/null
}

ensure_container() {
  ensure_volume

  if container_exists; then
    if container_is_running; then
      log "Using existing running container $DB_CONTAINER"
    else
      log "Starting existing container $DB_CONTAINER"
      "$CLI_BIN" start "$DB_CONTAINER" >/dev/null
    fi
  else
    create_container
  fi
}

wait_for_postgres() {
  log "Waiting for PostgreSQL to accept connections"
  for _ in $(seq 1 30); do
    if "$CLI_BIN" exec "$DB_CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "PostgreSQL did not become ready in time." >&2
  exit 1
}

apply_schema() {
  if [[ ! -f "$SCHEMA_FILE" ]]; then
    echo "Schema file not found: $SCHEMA_FILE" >&2
    exit 1
  fi

  log "Applying schema from $SCHEMA_FILE"
  "$CLI_BIN" exec -i "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" < "$SCHEMA_FILE"
}

apply_base_seed() {
  if [[ ! -f "$BASE_SEED_FILE" ]]; then
    echo "Base seed file not found: $BASE_SEED_FILE" >&2
    exit 1
  fi

  log "Applying base seed data from $BASE_SEED_FILE"
  "$CLI_BIN" exec -i "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" < "$BASE_SEED_FILE"
}

apply_test_seed() {
  if [[ "$APPLY_TEST_SEED" != "1" ]]; then
    return 0
  fi

  if [[ ! -f "$TEST_SEED_FILE" ]]; then
    echo "Test seed file not found: $TEST_SEED_FILE" >&2
    exit 1
  fi

  log "Applying test seed data from $TEST_SEED_FILE"
  "$CLI_BIN" exec -i "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" < "$TEST_SEED_FILE"
}

apply_real_seed() {
  if [[ "$APPLY_REAL_SEED" != "1" ]]; then
    return 0
  fi

  if [[ ! -f "$REAL_SEED_FILE" ]]; then
    echo "Real seed file not found: $REAL_SEED_FILE" >&2
    exit 1
  fi

  log "Applying real seed data from $REAL_SEED_FILE"
  "$CLI_BIN" exec -i "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" < "$REAL_SEED_FILE"
}

reset_data() {
  log "Resetting database data in $DB_NAME"
  "$CLI_BIN" exec -i "$DB_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" <<SQL
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO "$DB_USER";
GRANT ALL ON SCHEMA public TO public;
SQL
}

open_psql() {
  exec "$CLI_BIN" exec -it "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME"
}

stop_container() {
  if container_exists && container_is_running; then
    log "Stopping container $DB_CONTAINER"
    "$CLI_BIN" stop "$DB_CONTAINER" >/dev/null
  else
    log "Container $DB_CONTAINER is not running"
  fi
}

select_runtime

case "$ACTION" in
  up)
    ensure_container
    wait_for_postgres
    apply_schema
    apply_base_seed
    apply_real_seed
    apply_test_seed
    log "Database ready at postgresql://$DB_USER:*****@localhost:$DB_PORT/$DB_NAME"
    ;;
  reset-data)
    ensure_container
    wait_for_postgres
    reset_data
    apply_schema
    apply_base_seed
    apply_real_seed
    apply_test_seed
    log "Database data reset at postgresql://$DB_USER:*****@localhost:$DB_PORT/$DB_NAME"
    ;;
  down)
    stop_container
    ;;
  psql)
    ensure_container
    wait_for_postgres
    open_psql
    ;;
  *)
    echo "Usage: $0 {up|reset-data|down|psql}" >&2
    exit 1
    ;;
esac
