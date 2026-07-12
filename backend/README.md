# Backend — Architectural Bottlenecks & Fixes

This document records **bottlenecks we found** in the original collaboration design, **why they were a problem**, and **how we fixed them**. It is meant for reviewers and interview prep — not a full API reference.

For stress-testing the fixed architecture (dual instances, Locust, reflector), see [`../stress/README.md`](../stress/README.md).

---

## Bottleneck 1: In-process connection maps (no horizontal scale)

### What was wrong

Editors were tracked in Python process memory:

```python
document_connections: Dict[str, Set[WebSocket]] = {}
creator_connections: Dict[str, WebSocket] = {}
```

When a client sent a `sync_update` or `awareness` message, the server looped those local sets and called `websocket.send_text(...)`. Approval notifications went only to `creator_connections[edit_key]` on **this** process.

### Why that was a problem

| Symptom | Root cause |
|---------|------------|
| Two users on different workers never see each other | Each Hypercorn/Uvicorn worker has its own empty `document_connections` |
| Sticky sessions required | Load balancer must pin a document’s users to one machine |
| Cannot scale out | Adding containers multiplies isolated “rooms,” not capacity |
| Creator approval fragile | Creator on worker A never hears pending editors on worker B |

In short: the broadcast layer was **tied to a single OS process**. That is fine for a demo; it breaks the moment you run `N > 1` workers.

### How we fixed it — Distributed Redis Pub/Sub

1. **Removed** `document_connections` and `creator_connections`.
2. On every outbound collab message, **publish** to Redis channel `channel:{edit_key}` (including approval requests).
3. On connect, each socket **subscribes** and runs a background `redis_listener` that forwards channel messages to that WebSocket.
4. Tag each publish with `_sid` (connection UUID); listeners **drop echoes** so a sender does not receive their own message back.

```
User A (Worker 1) ──publish──► Redis channel:{edit_key}
                                      │
User B (Worker 2) ◄──subscribe────────┘
         redis_listener ──► websocket.send_text()
```

**Result:** Workers are interchangeable. Cross-instance latency under stress (reflector A on `:8000`, B on `:8001`) measured ~1–2 ms with zero misses.

**Key files:** `app/routes/collab.py` (`redis_listener`, `_publish`), `app/services/redis_service.py` (`publish_update`, `subscribe_to_document`).

---

## Bottleneck 2: Fragile CRDT merge under noisy / invalid log entries

### What was wrong

Document edits are stored as an append-only Redis list of binary Yjs updates. On join or flush, `merge_crdt_updates` replayed every list entry into a `pycrdt.Doc` via `apply_update`.

During stress testing, the reflector injected **marker payloads** (plain strings like `STRESS_MARKER:...`) into that same log to measure A→B latency. Those bytes are **not** valid Yjs frames.

### Why that was a problem

| Symptom | Root cause |
|---------|------------|
| WebSocket close `1011` / join crash | `get_merged_crdt_state` ran on connect and panicked mid-merge |
| Event loop / request path aborted | `pycrdt` raised `PanicException` (`EndOfBuffer`) on corrupt bytes |
| One bad frame poisoned the room | A single non-Yjs entry in `doc:updates:{key}` blocked **all** new editors |
| Hard to notice in unit tests alone | Happy-path merges never mixed stress noise with real deltas |

Worse: `PanicException` subclasses **`BaseException`**, not `Exception` — so a naive `except Exception` still let the panic escape.

### How we fixed it — CRDT merge hardening

`merge_crdt_updates` now:

1. Tries `apply_update` per entry.
2. On failure (`Exception` **or** non-system `BaseException` such as `PanicException`), **logs and skips** that frame.
3. Continues merging remaining valid deltas.
4. Returns `None` only if **nothing** applied successfully.

Valid Yjs traffic is preserved; garbage stress markers no longer take down the room.

**Regression tests:** `tests/unit/test_redis_service.py`  
(`test_merge_crdt_updates_skips_corrupt_frames_without_crashing`,  
`test_merge_crdt_updates_returns_none_when_all_frames_are_corrupt`)

**Key file:** `app/services/redis_service.py` (`merge_crdt_updates`).

---

## Bottleneck 3: Synchronous CRDT merge blocking the event loop

### What was wrong

`merge_crdt_updates` runs a CPU-bound loop (`doc.apply_update(update)`) synchronously on the main Python thread. Every join, flush, and viewer snapshot called it inline from async handlers.

On the WebTransport path (`aioquic` single-threaded asyncio loop), a large merge blocked QUIC packet handling, Redis Pub/Sub routing, and new connection acceptance.

### Why that was a problem

| Symptom | Root cause |
|---------|------------|
| Rising asyncio loop lag under load | Merge held the event loop for milliseconds per document |
| QUIC/WebSocket stalls during join | `get_merged_crdt_state` ran sync merge after `LRANGE` |
| Worse with long-lived sessions | Unbounded log growth (Bottleneck 4) made merges longer over time |

### How we fixed it — Thread-pool offload

All async call sites now merge off the event loop:

```python
merged = await asyncio.to_thread(merge_crdt_updates, updates)
```

Applied in `get_merged_crdt_state`, `flush_to_postgres`, `persist_crdt_to_postgres`, and `maybe_checkpoint_crdt_log`.

**Key files:** `app/services/redis_service.py`, `app/routes/collab.py`.

---

## Bottleneck 4: Unbounded Redis append-log growth

### What was wrong

Every keystroke appended a binary delta to `doc:updates:{edit_key}`. The list was only compacted when the last editor disconnected. Long-lived tabs or busy documents let the list grow without bound.

On join, `get_merged_crdt_state` pulled the entire list via `LRANGE 0 -1` and merged from scratch — O(n) memory and CPU per connect.

### Why that was a problem

| Symptom | Root cause |
|---------|------------|
| Slow editor joins on active docs | Full replay of hundreds/thousands of deltas |
| Memory spikes in Redis and Python | Entire log loaded for every merge |
| Compounding with Bottleneck 3 | Longer merges blocked the loop longer |

### How we fixed it — Periodic checkpointing

When `LLEN doc:updates:{edit_key} >= CRDT_CHECKPOINT_THRESHOLD` (default **100**):

1. Acquire a short-lived Redis lock (`doc:checkpoint_lock:{edit_key}`).
2. `LRANGE` → `asyncio.to_thread(merge_crdt_updates)` → replace the list with a **single** merged baseline via `cache_crdt_state`.

Checkpoint runs automatically after each `append_crdt_update` (cheap `LLEN` short-circuit when below threshold).

**Config:** `CRDT_CHECKPOINT_THRESHOLD` in `app/config.py`.

**Key file:** `app/services/redis_service.py` (`maybe_checkpoint_crdt_log`, `append_crdt_update`).

---

## Bottleneck 5: Delayed database persistence (data loss risk)

### What was wrong

While editors were connected, Redis was the sole live source of truth. Postgres was updated only when the last editor disconnected via `flush_to_postgres`. `FLUSH_INTERVAL_SECONDS` existed in config but was never wired.

### Why that was a problem

| Symptom | Root cause |
|---------|------------|
| All unflushed edits lost on Redis restart/OOM | No periodic persist while sessions active |
| Long editing sessions never hit Postgres | Flush tied exclusively to editor-count zero |
| Recovery depended on Redis durability alone | Postgres `crdt_state` could be hours stale |

### How we fixed it — Heartbeat Postgres persistence

Two persistence paths:

| Function | When | Redis log after |
|----------|------|-----------------|
| `persist_crdt_to_postgres` | Background worker every `FLUSH_INTERVAL_SECONDS` (default **60s**) for dirty docs | Replaced with single merged baseline (editors stay connected) |
| `flush_to_postgres` | Last editor disconnects (or session sweep finds empty room) | Cleared entirely |

Dirty documents tracked in Redis set `doc:dirty`; marked on every `append_crdt_update`, cleared after successful persist.

**Key files:** `app/routes/collab.py` (`persist_crdt_to_postgres`, `flush_to_postgres`), `app/main.py` (`_periodic_persist_loop` lifespan task).

---

## Bottleneck 6: O(N²) Pub/Sub amplification for awareness

### What was wrong

Every cursor movement published a separate Redis message on `channel:{edit_key}`. With N editors, each server-side listener processed every message — N editors × N messages per event ≈ O(N²) internal traffic per document.

WebTransport datagrams (unreliable, high-frequency) still went through Redis Pub/Sub one frame at a time.

### Why that was a problem

| Symptom | Root cause |
|---------|------------|
| Heavy Redis I/O at 50 editors | 50 listeners × 10 awareness events/s = 25,000 msg/s per doc |
| Event loop overhead on every instance | Each `redis_listener` deserialized and forwarded every frame |
| Stress test CPU climbed with user count | Pub/Sub fan-out dominated at high concurrency |

### How we fixed it — Awareness batching

Volatile cursor data is coalesced per document:

1. `enqueue_awareness(edit_key, data, connection_id)` buffers frames for `AWARENESS_BATCH_WINDOW_MS` (default **25ms**).
2. One Redis publish: `{type: "awareness_batch", cursors: [...]}`.
3. Receivers expand batches back into individual `{type: "awareness", data: ...}` frames — **no frontend changes**.

`sync_update` messages remain immediate (correctness).

**Key files:** `app/services/awareness_batcher.py`, `app/routes/collab.py`, `app/webtransport_server.py`.

---

## Bottleneck 7: Zombie editor sessions on ungraceful disconnect

### What was wrong

Active editors tracked via simple Redis `INCR`/`DECR` on `doc:editors:{edit_key}`. Cleanup ran only in WebSocket `finally` or WebTransport `close()`.

If QUIC died without lifecycle hooks (power loss, network partition), the counter stayed above zero forever — no final Postgres flush, Redis log never cleared, room could appear permanently full.

### Why that was a problem

| Symptom | Root cause |
|---------|------------|
| Document never flushes after crash | Counter never reached zero |
| 50-editor cap falsely triggered | Stale sessions counted as active |
| WebTransport gap | No handler for `ConnectionTerminated` |

### How we fixed it — TTL session tracking

Replaced counter with session sets and expiring heartbeat keys:

| Redis key | Purpose |
|-----------|---------|
| `doc:sessions:{edit_key}` | SET of connection UUIDs |
| `doc:session:{edit_key}:{sid}` | TTL key (default **30s**), refreshed every **10s** |
| `doc:active_edit_keys` | Global index for sweeper |

On connect: `register_editor_session` + per-connection heartbeat task.  
On disconnect / `ConnectionTerminated` / `connection_lost`: `unregister_editor_session`; if set empty → `flush_to_postgres`.

Lifespan sweeper (`_session_sweep_loop`) periodically drops sessions whose TTL key expired and flushes emptied documents.

**Config:** `SESSION_TTL_SECONDS`, `SESSION_HEARTBEAT_INTERVAL_SECONDS`, `SESSION_SWEEP_INTERVAL_SECONDS` in `app/config.py`.

**Key files:** `app/services/redis_service.py`, `app/routes/collab.py`, `app/webtransport_server.py`, `app/main.py`.

---

## Summary

| # | Bottleneck | Failure mode | Fix |
|---|------------|--------------|-----|
| 1 | In-memory WebSocket registries | Multi-worker sync broken | Redis Pub/Sub + `redis_listener` + `_sid` echo filter |
| 2 | Unguarded `pycrdt.apply_update` | Corrupt log entry crashes join/flush | Skip invalid frames; catch `PanicException` |
| 3 | Sync merge on event loop | QUIC/WS stalls during large merges | `asyncio.to_thread(merge_crdt_updates, ...)` |
| 4 | Unbounded Redis append log | Slow joins, memory spikes | Checkpoint at `LLEN >= 100` → single baseline blob |
| 5 | Flush only on last editor | Data loss on Redis failure | Periodic `persist_crdt_to_postgres` every 60s |
| 6 | Per-frame awareness Pub/Sub | O(N²) message amplification | 25ms awareness batching; expand on receive |
| 7 | INCR/DECR editor counter | Zombie sessions block flush | TTL session SET + heartbeat + sweeper |

More interview-oriented detail (simple explanations, diagrams, talking points) lives in [`../NOTES.md`](../NOTES.md) §20.
