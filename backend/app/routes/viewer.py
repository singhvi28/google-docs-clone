"""
SSE (Server-Sent Events) endpoint for read-only viewers.
Viewers receive bulk document state updates every 3 seconds.
"""
import asyncio
import logging
import base64

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.database import async_session_factory
from app.models import Document
from app.services.redis_service import get_cached_crdt_state, cache_crdt_state
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(tags=["viewer"])


@router.get("/api/view/{view_key}/stream")
async def viewer_sse(view_key: str, request: Request):
    """SSE stream for read-only viewers. Pushes state every 3s."""
    # Verify document exists
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

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                state = await get_viewer_crdt_state(edit_key)
                if state:
                    encoded = base64.b64encode(state).decode()
                    yield f"data: {{\"type\":\"state\",\"data\":\"{encoded}\"}}\n\n"
                else:
                    yield f"data: {{\"type\":\"heartbeat\"}}\n\n"
                await asyncio.sleep(settings.VIEWER_SYNC_INTERVAL_SECONDS)
        except asyncio.CancelledError:
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


async def get_viewer_crdt_state(edit_key: str) -> bytes | None:
    """Load the freshest viewer state from Redis, then persisted storage."""
    state = await get_cached_crdt_state(edit_key)
    if state:
        return state

    async with async_session_factory() as db:
        result = await db.execute(
            select(Document).where(Document.edit_key == edit_key)
        )
        doc = result.scalar_one_or_none()
        if not doc or not doc.crdt_state:
            return None

        await cache_crdt_state(edit_key, doc.crdt_state)
        return doc.crdt_state
