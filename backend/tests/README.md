# Backend Test Suite

Unit and integration tests for the FastAPI backend, using `pytest` and `pytest-asyncio`.

## Testing Architecture & Isolation

The production backend depends on PostgreSQL and Redis, but the test suite is **service-free** — no Docker containers or external databases required. Tests run quickly in local development and CI.

- **Database isolation**: An in-memory `FakeStore` / `FakeSession` (see `conftest.py`) replaces PostgreSQL. FastAPI's `get_db` dependency is overridden to inject this fake session. `collab` and `viewer` routes also get `async_session_factory` patched to the same store.
- **Redis isolation**: A patched in-memory `FakeRedis` (in `test_redis_service.py`) implements lists (`RPUSH`/`LRANGE`), sets (`SADD`/`SCARD`/`SMEMBERS`), hashes, TTL keys (`SET`/`EXPIRE`), checkpoint locks (`SET NX`), dirty-document tracking, and pub/sub publish — no real Redis server.
- **Pub/Sub isolation**: Collab and viewer tests use lightweight `FakePubSub` objects that yield scripted messages instead of connecting to Redis.
- **Auth overrides**: Authenticated routes override `get_current_user`, so fixtures can inject mock users without OAuth tokens.

## What the Tests Cover

The suite validates the production collaboration architecture:

| Area | Behavior under test |
|------|---------------------|
| **CRDT update log** | Yjs updates are appended (`RPUSH`), merged via `pycrdt` (offloaded with `asyncio.to_thread`), and truncated on final flush |
| **Log checkpointing** | When `LLEN >= CRDT_CHECKPOINT_THRESHOLD`, the log compacts to a single merged baseline blob |
| **Corrupt frame resilience** | `merge_crdt_updates` skips non-Yjs / truncated payloads (`PanicException`) without crashing; keeps valid deltas |
| **Distributed Pub/Sub** | WebSocket `redis_listener` forwards channel messages, skips echo via `_sid`, and expands `awareness_batch` into per-cursor frames |
| **Awareness batching** | Multiple cursor events within the batch window coalesce into one Redis publish |
| **Postgres flush (last editor)** | Last session leaving merges the Redis log, persists to `documents.crdt_state`, and clears the log |
| **Periodic persist** | `persist_crdt_to_postgres` writes to Postgres and replaces the Redis log with a baseline while editors may still be connected |
| **TTL session tracking** | Editor sessions registered in a Redis SET with expiring heartbeat keys; stale sessions swept; count via `SCARD` |
| **Event-driven SSE** | Viewers receive one initial snapshot, then live `sync_update` deltas from Pub/Sub (no polling loop) |

## Directory Layout

### `unit/`

Isolated logic tests — no full HTTP round-trips.

- **`test_auth.py`**: JWT encoding/decoding and token expiration.
- **`test_collab.py`**:
  - `redis_listener` — forwards Pub/Sub messages to a WebSocket, filters out the sender's own `_sid` echo, and expands `awareness_batch` payloads.
  - `flush_to_postgres` — merges accumulated CRDT updates into Postgres and clears the Redis log; no-ops when the log is empty.
  - `persist_crdt_to_postgres` — merges and writes to Postgres, then caches a single baseline blob in Redis (does not clear the log).
- **`test_redis_service.py`**:
  - Append-only CRDT log (`append_crdt_update`, `get_crdt_updates`, `merge_crdt_updates`, dirty-document tracking).
  - **Threaded merge** — `get_merged_crdt_state` returns correct merged state via `asyncio.to_thread`.
  - **Corrupt-frame regression** — `merge_crdt_updates` must not crash when the Redis log contains non-Yjs bytes (e.g. stress `STRESS_MARKER:...` payloads or truncated frames). pycrdt raises `PanicException` (`BaseException`, not `Exception`); the merge loop catches it, skips the bad entry, and still merges valid deltas. All-corrupt input returns `None`.
  - **Log checkpointing** — `test_checkpoint_compacts_log_at_threshold` verifies the log compacts to one entry when threshold is reached.
  - **TTL sessions** — register, heartbeat, unregister, and stale-session sweep (`test_editor_sessions_register_heartbeat_and_unregister`, `test_sweep_stale_editor_sessions`).
  - Baseline seeding (`seed_crdt_state_if_empty`).
  - Backward-compatible cache helpers (`cache_crdt_state`, `get_cached_crdt_state`).
  - Legacy editor counter wrappers (`increment_editor_count` / `decrement_editor_count`), pending-approval queue, and `publish_update`.
  - **Awareness batcher** — coalesces multiple awareness frames into one publish; `expand_awareness_messages` skips echo and expands batches.
- **`test_utils.py`**: Moniker generation and cursor color formatting.

### `integration/`

Boot an `httpx.AsyncClient` (ASGI transport) against the FastAPI app.

- **`test_auth_routes.py`**: `/api/auth/me` returns the authenticated user profile.
- **`test_documents_routes.py`**: Document REST lifecycle — create, list (Created/Edited/Viewed tabs), lookup by edit/view keys, title updates, and deletion permissions.
- **`test_health_and_viewer.py`**:
  - `/api/health` responds with the expected payload.
  - SSE viewer stream returns 404 for unknown view keys.
  - Initial merged CRDT state is pushed once on connect.
  - Live `sync_update` events are forwarded from a fake Pub/Sub subscription (awareness messages are filtered out).
  - Persisted Postgres state is used when the Redis log is empty, and the log is seeded for subsequent editors.

### `conftest.py`

Shared fixtures:

- `session_factory` — in-memory fake database session factory.
- `make_user`, `make_document`, `make_permission` — factory helpers for seeding data.
- `client` — ASGI test client with `get_db`, `get_current_user`, and route-level session overrides applied.

## Running Tests

From the `backend/` directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -v
```

From the repository root (if the venv already exists):

```bash
backend/.venv/bin/python -m pytest backend -v
```

Run a single file or test:

```bash
pytest tests/unit/test_collab.py -v
pytest tests/unit/test_redis_service.py::test_merge_crdt_updates_skips_corrupt_frames_without_crashing -v
pytest tests/unit/test_redis_service.py::test_checkpoint_compacts_log_at_threshold -v
pytest tests/unit/test_redis_service.py::test_sweep_stale_editor_sessions -v
pytest tests/integration/test_health_and_viewer.py::test_viewer_sse_streams_live_sync_updates_from_pubsub -v
```

## Coverage Goals

- Keep tests zero-dependency so CI stays fast.
- Guard against route-order regressions (e.g. `/api/documents/by-edit-key/{key}` vs. dynamic segments).
- Enforce permission boundaries (creators delete, unauthorized users rejected).
- Prevent collaboration regressions: CRDT log corruption, broken Pub/Sub fan-out, and viewer polling reintroduction.
- Ensure join/flush survives corrupt Redis log entries (stress markers, truncated frames) without panicking the event loop.
- Verify checkpoint compaction, TTL session lifecycle, awareness batching, and periodic persist semantics.

## Not Covered Here

These require real infrastructure and are not part of this suite:

- End-to-end WebSocket or WebTransport sessions against a live server.
- Multi-process / multi-container Redis Pub/Sub fan-out.
- QUIC / HTTP/3 handshake and TLS certificate validation (`app/webtransport_server.py`).
- Lifespan background workers (`_periodic_persist_loop`, `_session_sweep_loop`) running against real Redis.
- Event-loop lag measurement under concurrent merges (`stress/loop_lag_probe.py`).

For manual verification of those paths, use `docker compose up` and the stress stack in [`../stress/README.md`](../stress/README.md).
