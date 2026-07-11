#!/bin/bash
set -euo pipefail

CERT_DIR="${CERT_DIR:-/app/certs}"
CERTFILE="${TLS_CERTFILE:-$CERT_DIR/cert.pem}"
KEYFILE="${TLS_KEYFILE:-$CERT_DIR/key.pem}"
WT_PORT="${WEBTRANSPORT_PORT:-4433}"

if [ ! -f "$CERTFILE" ] || [ ! -f "$KEYFILE" ]; then
  mkdir -p "$(dirname "$CERTFILE")"
  openssl req -x509 -newkey rsa:2048 \
    -keyout "$KEYFILE" \
    -out "$CERTFILE" \
    -days 365 -nodes \
    -subj "/CN=localhost" \
    -addext "subjectAltName = DNS:localhost,IP:127.0.0.1"
  echo "Generated self-signed TLS cert at $CERTFILE"
fi

# QUIC WebTransport coordination layer (UDP + TLS — browsers require HTTPS for WT)
python -m app.webtransport_server \
  --certificate "$CERTFILE" \
  --key "$KEYFILE" \
  --host 0.0.0.0 \
  --port "$WT_PORT" &

# FastAPI over TCP: REST + WebSocket fallback (plain HTTP for local/dev clients)
exec hypercorn app.main:app \
  --bind 0.0.0.0:8000 \
  --worker-class uvloop
