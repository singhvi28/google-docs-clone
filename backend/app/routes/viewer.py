"""
SSE (Server-Sent Events) endpoint for read-only viewers.

Viewers receive an initial merged CRDT snapshot, then live deltas via the same
Redis Pub/Sub channel used by editors (event-driven, no polling).
"""
import base64
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.database import async_session_factory
from app.models import Document
from app.services.redis_service import (
    get_merged_crdt_state,
    seed_crdt_state_if_empty,
    subscribe_to_document,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["viewer"])


@router.get("/api/view/{view_key}/stream")
async def viewer_sse(view_key: str, request: Request):
    """SSE stream for read-only viewers — initial state then live Pub/Sub deltas."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(Document).where(Document.view_key == view_key)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            return StreamingResponse(
                iter(["data: {\"error\": \"not_found\"}\n\n"]),
                media_type="text/event-stream",
                status_code=404,
            )
        edit_key = doc.edit_key
        persisted_state = doc.crdt_state

    async def event_generator():
        pubsub = None
        try:
            if persisted_state:
                await seed_crdt_state_if_empty(edit_key, persisted_state)

            # Push initial merged state once so the page renders immediately
            state = await get_merged_crdt_state(edit_key)
            if not state and persisted_state:
                state = persisted_state
            if state:
                encoded = base64.b64encode(state).decode()
                yield f'data: {{"type":"state","data":"{encoded}"}}\n\n'
            else:
                yield 'data: {"type":"heartbeat"}\n\n'

            pubsub = await subscribe_to_document(edit_key)
            async for message in pubsub.listen():
                if await request.is_disconnected():
                    break
                if message["type"] != "message":
                    continue

                raw = message["data"]
                text = raw.decode() if isinstance(raw, bytes) else raw
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue

                # Viewers only need document deltas, not cursor awareness
                if parsed.get("type") != "sync_update":
                    continue
                parsed.pop("_sid", None)
                yield f"data: {json.dumps(parsed)}\n\n"
        except Exception as e:
            logger.error(f"Viewer SSE error: {e}")
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(f"channel:{edit_key}")
                    await pubsub.aclose()
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
