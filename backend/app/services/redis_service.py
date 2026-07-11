import logging
from typing import List, Optional

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Global Redis connection pool
_redis_pool: Optional[redis.Redis] = None


def _updates_key(edit_key: str) -> str:
    return f"doc:updates:{edit_key}"


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


# ─── Document CRDT Update Log (append-only) ───────────
async def append_crdt_update(edit_key: str, update: bytes) -> None:
    """Append a Yjs incremental update to the document's Redis log."""
    r = await get_redis()
    await r.rpush(_updates_key(edit_key), update)


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
    for update in updates:
        doc.apply_update(update)
    return doc.get_update()


async def get_merged_crdt_state(edit_key: str) -> Optional[bytes]:
    """Merge the Redis update log into a single CRDT state blob."""
    return merge_crdt_updates(await get_crdt_updates(edit_key))


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
    await append_crdt_update(edit_key, state)


async def get_cached_crdt_state(edit_key: str) -> Optional[bytes]:
    """Retrieve the merged CRDT state from the Redis update log."""
    return await get_merged_crdt_state(edit_key)


async def delete_crdt_state(edit_key: str) -> None:
    """Remove the cached CRDT update log."""
    await clear_crdt_updates(edit_key)


# ─── Active Editor Tracking ──────────────────────────
async def get_active_editor_count(edit_key: str) -> int:
    """Get the number of active editors for a document."""
    r = await get_redis()
    count = await r.get(f"doc:editors:{edit_key}")
    return int(count) if count else 0


async def increment_editor_count(edit_key: str) -> int:
    """Increment and return the active editor count."""
    r = await get_redis()
    return await r.incr(f"doc:editors:{edit_key}")


async def decrement_editor_count(edit_key: str) -> int:
    """Decrement and return the active editor count."""
    r = await get_redis()
    count = await r.decr(f"doc:editors:{edit_key}")
    if count <= 0:
        await r.delete(f"doc:editors:{edit_key}")
        return 0
    return count


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
