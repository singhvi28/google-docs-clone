FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (openssl for TLS cert generation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

RUN mkdir -p /app/certs && chmod +x /app/start.sh

# TCP for REST/WebSocket; UDP for QUIC/WebTransport
EXPOSE 8000 4433/udp

CMD ["./start.sh"]
