#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-up}"
NERDCTL_BIN="${NERDCTL_BIN:-nerdctl}"
CLI_BIN=""
ADMINER_CONTAINER="${ADMINER_CONTAINER:-pf-payroll-adminer}"
ADMINER_PORT="${ADMINER_PORT:-8080}"

log() {
  printf '[adminer] %s\n' "$1"
}

# shellcheck source=scripts/shared/container_runtime.sh
source "$(dirname "$0")/shared/container_runtime.sh"

container_exists() {
  "$CLI_BIN" container inspect "$ADMINER_CONTAINER" >/dev/null 2>&1
}

container_is_running() {
  [[ "$("$CLI_BIN" inspect --format '{{.State.Status}}' "$ADMINER_CONTAINER" 2>/dev/null)" == "running" ]]
}

configured_port() {
  "$CLI_BIN" inspect --format '{{with index .HostConfig.PortBindings "8080/tcp"}}{{(index . 0).HostPort}}{{end}}' "$ADMINER_CONTAINER" 2>/dev/null || true
}

published_port() {
  "$CLI_BIN" port "$ADMINER_CONTAINER" 8080/tcp 2>/dev/null | awk -F: 'NR==1 {print $NF}'
}

port_is_free() {
  python3 - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(1)
raise SystemExit(0)
PY
}

find_free_port() {
  python3 - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
for candidate in range(port, port + 200):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", candidate))
        except OSError:
            continue
        print(candidate)
        raise SystemExit(0)

raise SystemExit(1)
PY
}

create_container() {
  local port
  port="$(find_free_port "$ADMINER_PORT")" || {
    echo "Could not find a free port for Adminer starting at $ADMINER_PORT." >&2
    exit 1
  }

  log "Creating Adminer container $ADMINER_CONTAINER on port $port"
  "$CLI_BIN" run -d \
    --name "$ADMINER_CONTAINER" \
    --restart unless-stopped \
    -p "$port:8080" \
    -e ADMINER_DEFAULT_SERVER=host.docker.internal \
    adminer >/dev/null

  printf 'http://localhost:%s\n' "$port"
}

recreate_container() {
  log "Recreating Adminer container $ADMINER_CONTAINER with a free port"
  "$CLI_BIN" rm -f "$ADMINER_CONTAINER" >/dev/null
  create_container
}

ensure_container() {
  if container_exists; then
    if container_is_running; then
      local port
      port="$(published_port)"
      if [[ -z "$port" ]]; then
        recreate_container
        return
      fi

      log "Using existing running container $ADMINER_CONTAINER"
      printf 'http://localhost:%s\n' "$port"
      return
    fi

    local port
    port="$(configured_port)"
    if [[ -n "$port" ]] && port_is_free "$port"; then
      log "Starting existing container $ADMINER_CONTAINER on port $port"
      "$CLI_BIN" start "$ADMINER_CONTAINER" >/dev/null
      printf 'http://localhost:%s\n' "$port"
      return
    fi

    recreate_container
    return
  fi

  create_container
}

stop_container() {
  if container_exists && container_is_running; then
    log "Stopping container $ADMINER_CONTAINER"
    "$CLI_BIN" stop "$ADMINER_CONTAINER" >/dev/null
  else
    log "Container $ADMINER_CONTAINER is not running"
  fi
}

select_runtime

case "$ACTION" in
  up)
    ensure_container
    ;;
  down)
    stop_container
    ;;
  *)
    echo "Usage: $0 {up|down}" >&2
    exit 1
    ;;
esac
