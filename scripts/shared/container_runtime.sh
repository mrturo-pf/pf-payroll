#!/usr/bin/env bash
# Shared container-runtime detection for pf-payroll scripts.
# Source this file; callers must pre-declare CLI_BIN="" and a log() function.

runtime_available() {
  local candidate="$1"

  command -v "$candidate" >/dev/null 2>&1 && "$candidate" info >/dev/null 2>&1
}

select_runtime() {
  if [[ "$NERDCTL_BIN" != "auto" ]] && runtime_available "$NERDCTL_BIN"; then
    CLI_BIN="$NERDCTL_BIN"
  elif runtime_available nerdctl; then
    CLI_BIN="nerdctl"
  elif runtime_available docker; then
    CLI_BIN="docker"
  else
    echo "No working Rancher Desktop container CLI was found. Start Rancher Desktop and ensure either nerdctl or docker is available." >&2
    exit 1
  fi

  log "Using container runtime $CLI_BIN"
}
