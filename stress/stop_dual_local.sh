#!/usr/bin/env bash
set -euo pipefail

STRESS="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="${STRESS}/.pids"

stop_one() {
  local name="$1"
  local pid_file="${PID_DIR}/${name}.pid"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    # start.sh execs hypercorn; WT child may be orphaned — kill process group if possible
    if kill -0 "$pid" 2>/dev/null; then
      echo "Stopping ${name} (pid ${pid})"
      kill "$pid" 2>/dev/null || true
      # Also kill any webtransport_server on known ports
      sleep 0.3
    fi
    rm -f "$pid_file"
  fi
}

stop_one instance1
stop_one instance2

# Best-effort cleanup of leftover WT / hypercorn from stress runs
pkill -f "app.webtransport_server" 2>/dev/null || true
pkill -f "hypercorn app.main:app" 2>/dev/null || true

echo "Dual instances stopped."
