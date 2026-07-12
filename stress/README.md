# Stress Testing — Horizontal Scale Validation

Blueprint from `CONTEXT.md`: run **two backend instances**, put **NGINX** in front, simulate editors, and measure **cross-instance broadcast latency** through Redis Pub/Sub.

```
                     ┌─────────────┐
 Locust / browsers ─►│ NGINX :8080 │──TCP──► backend1 :8000  + WT :4433
                     │      :4443  │──UDP──► backend2 :8001  + WT :4434
                     └─────────────┘              │
                                                  ▼
                                           Redis + Postgres
```

## Quick start (Docker)

```bash
# 1. Dual backends + Redis + Postgres + NGINX
docker compose -f stress/docker-compose.stress.yml up --build

# Host ports (avoids clashing with local Postgres/Redis):
#   LB REST/WS  :8080
#   backend1    :8000  WT :4433/udp
#   backend2    :8001  WT :4434/udp
#   Postgres    :5433
#   Redis       :6380

# 2. Install stress tooling into the backend venv
cd backend && python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r ../stress/requirements.txt

# 3. Seed JWT + document (point at Compose Postgres)
export DATABASE_URL='postgresql+asyncpg://gdocs:gdocs_secret@localhost:5433/gdocs_prod'
export REDIS_URL='redis://localhost:6380/0'
export JWT_SECRET='super-secret-dev-key-change-in-prod'
python ../stress/seed_stress_session.py --api http://localhost:8080
set -a && source ../stress/.session.env && set +a

# 4. E2E cross-instance latency (A on :8000, B on :8001)
python ../stress/reflector_latency.py --rounds 50

# 5. Load test through the LB
cd ../stress && locust -f locustfile.py --host http://localhost:8080
# Open http://localhost:8089 — start with ~20–50 users
```

## Quick start (local processes)

Requires Postgres + Redis already running (e.g. `docker compose up postgres redis`).

```bash
chmod +x stress/*.sh backend/start.sh

# Terminal 1 — two API/WT processes
./stress/run_dual_local.sh

# Terminal 2 — NGINX LB (system nginx or Docker --network host)
./stress/run_nginx.sh

# Terminal 3 — seed + reflector / Locust
backend/.venv/bin/pip install -r stress/requirements.txt
backend/.venv/bin/python stress/seed_stress_session.py
set -a && source stress/.session.env && set +a
backend/.venv/bin/python stress/reflector_latency.py
```

Stop local instances: `./stress/stop_dual_local.sh`

## What each piece does

| Artifact | Role |
|----------|------|
| `.env.instance1` / `.env.instance2` | Ports `8000/4433` vs `8001/4434`, shared DB/Redis |
| `run_dual_local.sh` | Boots both backends via `backend/start.sh` |
| `nginx.conf` | Local LB: HTTP `8080`, UDP WT `4443` |
| `nginx.docker.conf` | Same for Compose service names |
| `docker-compose.stress.yml` | Full dual-stack in Docker |
| `seed_stress_session.py` | Creates user + JWT + doc → `.session.env` |
| `locustfile.py` | WebSocket typing load through LB; optional WT |
| `reflector_latency.py` | **Accurate** A→Redis→B latency (must-run) |
| `metrics.sh` | Redis ops/sec, update-log length, CPU |
| `loop_lag_probe.py` | Helper notes for asyncio lag under load |

## Endpoints

| Target | URL |
|--------|-----|
| LB REST | `http://localhost:8080/api/...` |
| LB WebSocket | `ws://localhost:8080/ws/doc/{edit_key}?token=...` |
| LB WebTransport | UDP `localhost:4443` (Compose) / `4443` (local nginx) |
| Instance 1 direct | `http://localhost:8000` + UDP `4433` |
| Instance 2 direct | `http://localhost:8001` + UDP `4434` |

## Metrics to watch

While Locust runs, in another terminal:

```bash
# Pass edit_key from .session.env
set -a && source stress/.session.env && set +a
./stress/metrics.sh "$STRESS_EDIT_KEY"
```

Check:

1. **Redis** — `instantaneous_ops_per_sec`, `LLEN doc:updates:{edit_key}` growth
2. **CPU split** — both `hypercorn` / backend containers should share load
3. **Reflector p50/p95** — true Pub/Sub broadcast latency across instances
4. **Misses** — rising misses ⇒ loop blocked, Redis down, or instances not sharing Redis

Optional:

```bash
backend/.venv/bin/python stress/loop_lag_probe.py
redis-cli MONITOR   # noisy; use briefly
```

## Locust notes

- Default user class uses **WebSocket** (works through NGINX TCP LB and validates horizontal fan-out).
- Enable experimental WebTransport user: `STRESS_USE_WT=1 locust -f locustfile.py ...`
- For **broadcast latency**, prefer `reflector_latency.py` over Locust charts (Locust measures send time, not A→B).

## Instance env reference

**Instance 1** (`stress/.env.instance1`):

```env
BACKEND_URL=http://localhost:8000
HTTP_PORT=8000
WEBTRANSPORT_PORT=4433
DATABASE_URL=postgresql+asyncpg://gdocs:gdocs_secret@localhost:5432/gdocs_prod
REDIS_URL=redis://localhost:6379/0
```

**Instance 2** (`stress/.env.instance2`):

```env
BACKEND_URL=http://localhost:8001
HTTP_PORT=8001
WEBTRANSPORT_PORT=4434
# same DATABASE_URL + REDIS_URL
```

`backend/start.sh` honors `HTTP_PORT` and `WEBTRANSPORT_PORT`.

## Success criteria

- Reflector reports low miss rate with both instances up and sharing Redis
- Locust can sustain concurrent WS editors through `:8080` without error spikes
- `metrics.sh` shows both backend processes under CPU load
- Killing one backend still allows the other to serve (LB removes dead peer after failures)

## Cleanup

```bash
./stress/stop_dual_local.sh
docker compose -f stress/docker-compose.stress.yml down -v
rm -f stress/.session.env
```
