import asyncio
import logging
from typing import List, Optional, Set

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Global Redis connection pool
_redis_pool: Optional[redis.Redis] = None

DIRTY_SET_KEY = "doc:dirty"
ACTIVE_DOCS_KEY = "doc:active_edit_keys"


def _updates_key(edit_key: str) -> str:
    return f"doc:updates:{edit_key}"


def _sessions_key(edit_key: str) -> str:
    return f"doc:sessions:{edit_key}"


def _session_ttl_key(edit_key: str, session_id: str) -> str:
    return f"doc:session:{edit_key}:{session_id}"


def _checkpoint_lock_key(edit_key: str) -> str:
    return f"doc:checkpoint_lock:{edit_key}"


async def get_redis() -> redis.Redis:
    """Get or create the global Redis connection."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=False,  # We handle binary CRDT data
        )
    return _redis_pool


async def close_redis():
    """Close the Redis connection pool."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.close()
        _redis_pool = None


def _decode_member(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


# ─── Document CRDT Update Log (append-only) ───────────
async def append_crdt_update(edit_key: str, update: bytes) -> None:
    """Append a Yjs incremental update to the document's Redis log."""
    r = await get_redis()
    await r.rpush(_updates_key(edit_key), update)
    await mark_document_dirty(edit_key)
    await maybe_checkpoint_crdt_log(edit_key)


async def get_crdt_updates(edit_key: str) -> List[bytes]:
    """Return all accumulated binary updates for a document."""
    r = await get_redis()
    return await r.lrange(_updates_key(edit_key), 0, -1)


async def clear_crdt_updates(edit_key: str) -> None:
    """Truncate the Redis update log."""
    r = await get_redis()
    await r.delete(_updates_key(edit_key))


def merge_crdt_updates(updates: List[bytes]) -> Optional[bytes]:
    """Merge a sequence of Yjs updates into a single state block via pycrdt."""
    if not updates:
        return None
    # Single entry is already a complete state (or baseline seed) — no merge needed
    if len(updates) == 1:
        return updates[0]
    from pycrdt import Doc

    doc = Doc()
    applied = 0
    for update in updates:
        try:
            doc.apply_update(update)
            applied += 1
        except Exception:
            # Skip corrupt / non-Yjs payloads (e.g. stress-test markers)
            logger.warning("Skipping invalid CRDT update (%d bytes)", len(update))
        except BaseException as exc:
            # pycrdt raises PanicException (BaseException, not Exception) on truncated frames
            if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            logger.warning(
                "Skipping invalid CRDT update (%d bytes): %s",
                len(update),
                type(exc).__name__,
            )
    if applied == 0:
        return None
    return doc.get_update()


async def get_merged_crdt_state(edit_key: str) -> Optional[bytes]:
    """Merge the Redis update log into a single CRDT state blob (off event loop)."""
    updates = await get_crdt_updates(edit_key)
    return await asyncio.to_thread(merge_crdt_updates, updates)


async def maybe_checkpoint_crdt_log(edit_key: str) -> bool:
    """
    When the append log reaches CRDT_CHECKPOINT_THRESHOLD entries, merge it
    into a single baseline blob so join/flush stays O(1) in list length.
    """
    r = await get_redis()
    key = _updates_key(edit_key)
    length = await r.llen(key)
    if length < settings.CRDT_CHECKPOINT_THRESHOLD:
        return False

    lock_key = _checkpoint_lock_key(edit_key)
    acquired = await r.set(lock_key, b"1", nx=True, ex=10)
    if not acquired:
        return False

    try:
        updates = await get_crdt_updates(edit_key)
        if len(updates) < settings.CRDT_CHECKPOINT_THRESHOLD:
            return False
        merged = await asyncio.to_thread(merge_crdt_updates, updates)
        if not merged:
            return False
        await cache_crdt_state(edit_key, merged)
        logger.info(
            "Checkpointed CRDT log for %s (%d → 1 entries)",
            edit_key,
            len(updates),
        )
        return True
    finally:
        await r.delete(lock_key)


async def seed_crdt_state_if_empty(edit_key: str, state: bytes) -> None:
    """Seed the update log with a baseline state when the log is empty."""
    r = await get_redis()
    key = _updates_key(edit_key)
    if await r.llen(key) == 0:
        await r.rpush(key, state)


# Backward-compatible aliases used by older call sites / tests
async def cache_crdt_state(edit_key: str, state: bytes) -> None:
    """Replace the update log with a single baseline state."""
    await clear_crdt_updates(edit_key)
    r = await get_redis()
    await r.rpush(_updates_key(edit_key), state)


async def get_cached_crdt_state(edit_key: str) -> Optional[bytes]:
    """Retrieve the merged CRDT state from the Redis update log."""
    return await get_merged_crdt_state(edit_key)


async def delete_crdt_state(edit_key: str) -> None:
    """Remove the cached CRDT update log."""
    await clear_crdt_updates(edit_key)


# ─── Dirty-document tracking (periodic Postgres flush) ─
async def mark_document_dirty(edit_key: str) -> None:
    r = await get_redis()
    await r.sadd(DIRTY_SET_KEY, edit_key)


async def clear_document_dirty(edit_key: str) -> None:
    r = await get_redis()
    await r.srem(DIRTY_SET_KEY, edit_key)


async def get_dirty_documents() -> List[str]:
    r = await get_redis()
    members = await r.smembers(DIRTY_SET_KEY)
    return [_decode_member(m) for m in members]


# ─── Active Editor Tracking (TTL sessions) ────────────
async def get_active_editor_count(edit_key: str) -> int:
    """Get the number of active editors for a document."""
    r = await get_redis()
    return int(await r.scard(_sessions_key(edit_key)))


async def register_editor_session(edit_key: str, session_id: str) -> int:
    """Register a live editor session and return the new active count."""
    r = await get_redis()
    await r.sadd(_sessions_key(edit_key), session_id)
    await r.set(
        _session_ttl_key(edit_key, session_id),
        b"1",
        ex=settings.SESSION_TTL_SECONDS,
    )
    await r.sadd(ACTIVE_DOCS_KEY, edit_key)
    return await get_active_editor_count(edit_key)


async def heartbeat_editor_session(edit_key: str, session_id: str) -> bool:
    """Refresh the session TTL. Returns False if the session is no longer registered."""
    r = await get_redis()
    if not await r.sismember(_sessions_key(edit_key), session_id):
        return False
    await r.set(
        _session_ttl_key(edit_key, session_id),
        b"1",
        ex=settings.SESSION_TTL_SECONDS,
    )
    return True


async def unregister_editor_session(edit_key: str, session_id: str) -> int:
    """Remove a session and return the remaining active count."""
    r = await get_redis()
    await r.srem(_sessions_key(edit_key), session_id)
    await r.delete(_session_ttl_key(edit_key, session_id))
    count = await get_active_editor_count(edit_key)
    if count <= 0:
        await r.delete(_sessions_key(edit_key))
        await r.srem(ACTIVE_DOCS_KEY, edit_key)
        return 0
    return count


async def sweep_stale_editor_sessions() -> List[str]:
    """
    Drop sessions whose TTL key expired. Returns edit_keys that became empty
    (callers should flush those documents to Postgres).
    """
    r = await get_redis()
    empty_keys: List[str] = []
    active = await r.smembers(ACTIVE_DOCS_KEY)
    for ek_raw in active:
        edit_key = _decode_member(ek_raw)
        sessions = await r.smembers(_sessions_key(edit_key))
        for sid_raw in sessions:
            sid = _decode_member(sid_raw)
            if not await r.exists(_session_ttl_key(edit_key, sid)):
                await r.srem(_sessions_key(edit_key), sid)
                logger.info("Swept stale editor session %s on %s", sid, edit_key)
        if await get_active_editor_count(edit_key) == 0:
            await r.delete(_sessions_key(edit_key))
            await r.srem(ACTIVE_DOCS_KEY, edit_key)
            empty_keys.append(edit_key)
    return empty_keys


# Backward-compatible wrappers (generate ephemeral session ids when omitted)
async def increment_editor_count(edit_key: str, session_id: Optional[str] = None) -> int:
    """Increment and return the active editor count."""
    import uuid

    return await register_editor_session(edit_key, session_id or str(uuid.uuid4()))


async def decrement_editor_count(edit_key: str, session_id: Optional[str] = None) -> int:
    """
    Decrement and return the active editor count.

    Prefer unregister_editor_session with an explicit session_id. Without one,
    removes an arbitrary session (legacy test helper).
    """
    if session_id is not None:
        return await unregister_editor_session(edit_key, session_id)

    r = await get_redis()
    members: Set = await r.smembers(_sessions_key(edit_key))
    if not members:
        return 0
    sid = _decode_member(next(iter(members)))
    return await unregister_editor_session(edit_key, sid)


# ─── Pub/Sub for Real-Time Sync ──────────────────────
async def publish_update(edit_key: str, data: bytes) -> None:
    """Publish a CRDT update to the document's channel."""
    r = await get_redis()
    await r.publish(f"channel:{edit_key}", data)


async def subscribe_to_document(edit_key: str):
    """Subscribe to a document's update channel. Returns a pubsub object."""
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(f"channel:{edit_key}")
    return pubsub


# ─── Approval Queue ──────────────────────────────────
async def add_pending_editor(edit_key: str, user_id: str, moniker: str) -> None:
    """Add a user to the pending approval queue for a document."""
    r = await get_redis()
    await r.hset(f"doc:pending:{edit_key}", user_id, moniker)
    # Auto-expire pending requests after 10 minutes
    await r.expire(f"doc:pending:{edit_key}", 600)


async def remove_pending_editor(edit_key: str, user_id: str) -> None:
    """Remove a user from the pending approval queue."""
    r = await get_redis()
    await r.hdel(f"doc:pending:{edit_key}", user_id)


async def get_pending_editors(edit_key: str) -> dict:
    """Get all pending editor requests for a document."""
    r = await get_redis()
    pending = await r.hgetall(f"doc:pending:{edit_key}")
    return {k.decode(): v.decode() for k, v in pending.items()} if pending else {}
