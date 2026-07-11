import logging
from typing import Optional

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Global Redis connection pool
_redis_pool: Optional[redis.Redis] = None


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


# ─── Document CRDT State ─────────────────────────────
async def cache_crdt_state(edit_key: str, state: bytes) -> None:
    """Cache the compiled CRDT document state in Redis."""
    r = await get_redis()
    await r.set(f"doc:crdt:{edit_key}", state)


async def get_cached_crdt_state(edit_key: str) -> Optional[bytes]:
    """Retrieve the cached CRDT state for a document."""
    r = await get_redis()
    return await r.get(f"doc:crdt:{edit_key}")


async def delete_crdt_state(edit_key: str) -> None:
    """Remove cached CRDT state when no longer needed."""
    r = await get_redis()
    await r.delete(f"doc:crdt:{edit_key}")


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
