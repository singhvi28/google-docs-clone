#!/usr/bin/env bash
# Start local NGINX load balancer (TCP REST/WS + UDP WebTransport).
set -euo pipefail

STRESS="$(cd "$(dirname "$0")" && pwd)"
CONF="${STRESS}/nginx.conf"

if command -v nginx >/dev/null 2>&1; then
  echo "Starting NGINX with ${CONF}"
  echo "  REST/WS LB:  http://localhost:8080"
  echo "  WT UDP LB:   udp://localhost:4443"
  nginx -c "$CONF" -g 'daemon off;'
elif command -v docker >/dev/null 2>&1; then
  echo "System nginx not found — running via Docker"
  docker run --rm --name gdocs-stress-nginx-local \
    --network host \
    -v "${CONF}:/etc/nginx/nginx.conf:ro" \
    nginx:1.27-alpine
else
  echo "Install nginx or Docker to run the load balancer."
  exit 1
fi
