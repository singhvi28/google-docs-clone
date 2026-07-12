#!/usr/bin/env bash
# Launch two local backend instances (shared Postgres + Redis) for horizontal-scale stress tests.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
STRESS="$ROOT/stress"
LOG_DIR="${STRESS}/logs"
PID_DIR="${STRESS}/.pids"

mkdir -p "$LOG_DIR" "$PID_DIR" "$BACKEND/certs"

load_env() {
  local file="$1"
  set -a
  # shellcheck disable=SC1090
  source "$file"
  set +a
}

start_instance() {
  local name="$1"
  local env_file="$2"
  load_env "$env_file"

  echo "Starting ${name}: HTTP :${HTTP_PORT}  WT UDP :${WEBTRANSPORT_PORT}"
  (
    cd "$BACKEND"
    export DATABASE_URL REDIS_URL JWT_SECRET FRONTEND_URL BACKEND_URL
    export HTTP_PORT WEBTRANSPORT_PORT TLS_CERTFILE TLS_KEYFILE CERT_DIR
    # Clear settings cache isn't needed — each process is fresh
    nohup ./start.sh >"${LOG_DIR}/${name}.log" 2>&1 &
    echo $! >"${PID_DIR}/${name}.pid"
  )
}

if [[ ! -x "$BACKEND/start.sh" ]]; then
  chmod +x "$BACKEND/start.sh"
fi

if [[ ! -d "$BACKEND/.venv" ]]; then
  echo "Create backend/.venv and install requirements first:"
  echo "  cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# Prefer venv python/openssl via PATH for child processes
export PATH="$BACKEND/.venv/bin:$PATH"

start_instance "instance1" "$STRESS/.env.instance1"
start_instance "instance2" "$STRESS/.env.instance2"

echo
echo "Waiting for health checks..."
for port in 8000 8001; do
  for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:${port}/api/health" >/dev/null; then
      echo "  :${port} healthy"
      break
    fi
    if [[ $i -eq 30 ]]; then
      echo "  :${port} failed to become healthy — see ${LOG_DIR}/"
      exit 1
    fi
    sleep 0.5
  done
done

echo
echo "Dual instances up."
echo "  Instance 1: http://localhost:8000  WT udp://localhost:4433"
echo "  Instance 2: http://localhost:8001  WT udp://localhost:4434"
echo "  Logs:       ${LOG_DIR}/"
echo "  Stop with:  ${STRESS}/stop_dual_local.sh"
echo
echo "Next: start NGINX LB with  ${STRESS}/run_nginx.sh"
echo "      seed a session with   ${STRESS}/seed_stress_session.py"
