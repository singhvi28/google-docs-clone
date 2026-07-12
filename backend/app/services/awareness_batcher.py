"""
Batch volatile awareness (cursor) frames before publishing to Redis.

Reduces O(N²) Pub/Sub amplification under many concurrent editors by coalescing
datagrams that arrive within a short window into a single channel message.
Receivers expand batches back into per-cursor `awareness` frames so clients
keep the same wire format.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services.redis_service import publish_update

logger = logging.getLogger(__name__)
settings = get_settings()

# edit_key → pending cursor entries
_buffers: Dict[str, List[Dict[str, Any]]] = {}
_flush_tasks: Dict[str, asyncio.Task] = {}
_lock = asyncio.Lock()


async def enqueue_awareness(
    edit_key: str,
    data: Any,
    connection_id: str,
) -> None:
    """Queue an awareness payload; flush as one Redis publish after the batch window."""
    entry = {"data": data, "_sid": connection_id}
    async with _lock:
        bucket = _buffers.setdefault(edit_key, [])
        bucket.append(entry)
        if edit_key not in _flush_tasks:
            delay = settings.AWARENESS_BATCH_WINDOW_MS / 1000.0
            _flush_tasks[edit_key] = asyncio.create_task(
                _flush_after_delay(edit_key, delay)
            )


async def _flush_after_delay(edit_key: str, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        await flush_awareness(edit_key)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Awareness batch flush failed for %s", edit_key)


async def flush_awareness(edit_key: str) -> Optional[bytes]:
    """Publish and clear the pending batch for edit_key. Returns published bytes (for tests)."""
    async with _lock:
        entries = _buffers.pop(edit_key, [])
        task = _flush_tasks.pop(edit_key, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    if not entries:
        return None

    payload = {
        "type": "awareness_batch",
        "cursors": [
            {"data": e["data"], "_sid": e["_sid"]} for e in entries
        ],
    }
    raw = json.dumps(payload).encode()
    await publish_update(edit_key, raw)
    return raw


def expand_awareness_messages(
    parsed: dict,
    connection_id: str,
) -> List[dict]:
    """
    Convert a Redis pub/sub payload into client-facing awareness frames,
    skipping echoes from connection_id.
    """
    msg_type = parsed.get("type")
    if msg_type == "awareness":
        if parsed.get("_sid") == connection_id:
            return []
        out = {k: v for k, v in parsed.items() if k != "_sid"}
        return [out]

    if msg_type == "awareness_batch":
        frames: List[dict] = []
        for cursor in parsed.get("cursors") or []:
            if cursor.get("_sid") == connection_id:
                continue
            frames.append({"type": "awareness", "data": cursor.get("data")})
        return frames

    return []


async def reset_batcher_for_tests() -> None:
    """Clear in-memory batch state (unit tests only)."""
    async with _lock:
        for task in _flush_tasks.values():
            task.cancel()
        _buffers.clear()
        _flush_tasks.clear()
