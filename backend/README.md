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

## Summary

| Bottleneck | Failure mode | Fix |
|------------|--------------|-----|
| In-memory WebSocket registries | Multi-worker / multi-container sync broken | Redis Pub/Sub + per-socket `redis_listener` + `_sid` echo filter |
| Unguarded `pycrdt.apply_update` | Corrupt log entry crashes join/flush | Skip invalid frames; merge valid ones; catch `PanicException` |

More interview-oriented detail (simple explanations, diagrams, talking points) lives in [`../NOTES.md`](../NOTES.md) §20.
