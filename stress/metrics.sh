#!/usr/bin/env bash
# Live metrics while stress-testing: Redis stats, update-log length, process CPU.
set -euo pipefail

EDIT_KEY="${1:-}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"

# Parse redis://host:port/db
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"

echo "=== Stress metrics (Ctrl+C to stop) ==="
echo "Redis: ${REDIS_HOST}:${REDIS_PORT}"
if [[ -n "$EDIT_KEY" ]]; then
  echo "Watching update log: doc:updates:${EDIT_KEY}"
fi
echo

while true; do
  ts="$(date '+%H:%M:%S')"
  echo "──── ${ts} ────"

  if command -v redis-cli >/dev/null 2>&1; then
    echo "[redis] info stats (ops/sec + pubsub)"
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" INFO stats 2>/dev/null \
      | grep -E '^(total_commands_processed|instantaneous_ops_per_sec|pubsub_channels|pubsub_patterns|keyspace)' \
      || echo "  (redis-cli INFO failed)"

    if [[ -n "$EDIT_KEY" ]]; then
      llen="$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" LLEN "doc:updates:${EDIT_KEY}" 2>/dev/null || echo '?')"
      editors="$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" GET "doc:editors:${EDIT_KEY}" 2>/dev/null || echo '0')"
      echo "[redis] doc:updates:${EDIT_KEY} LLEN=${llen}  editors=${editors:-0}"
    fi
  else
    echo "[redis] redis-cli not installed"
  fi

  echo "[cpu] hypercorn / webtransport processes"
  if command -v ps >/dev/null 2>&1; then
    ps -eo pid,pcpu,pmem,etime,cmd 2>/dev/null \
      | grep -E 'hypercorn app.main|webtransport_server' \
      | grep -v grep \
      || echo "  (no matching processes — are dual instances running?)"
  fi

  echo "[event-loop] tip: blocked asyncio shows as rising flush latency / missed reflector rounds"
  echo
  sleep 2
done
