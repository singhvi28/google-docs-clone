"""
Collaboration WebSocket endpoint for Yjs CRDT synchronization.

This implements a lightweight Yjs sync protocol over WebSocket:
- Clients send binary Yjs updates
- Server broadcasts updates to all other clients in the same document
- Server caches the merged CRDT state in Redis
- Periodic flush to PostgreSQL for persistence
"""
import asyncio
import logging
import json
from typing import Dict, Set
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session_factory
from app.models import Document, DocumentPermission, User
from app.routes.auth import decode_access_token
from app.services.redis_service import (
    get_active_editor_count, increment_editor_count,
    decrement_editor_count, cache_crdt_state, get_cached_crdt_state,
    add_pending_editor, remove_pending_editor, publish_update,
)
from app.utils import generate_moniker, generate_cursor_color
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(tags=["collaboration"])

# In-memory connection registry (per-process)
# Maps edit_key -> set of WebSocket connections
document_connections: Dict[str, Set[WebSocket]] = {}
# Maps edit_key -> creator's WebSocket (for approval notifications)
creator_connections: Dict[str, WebSocket] = {}


@router.websocket("/ws/doc/{edit_key}")
async def document_websocket(
    websocket: WebSocket,
    edit_key: str,
    token: str = Query(default=""),
):
    """
    WebSocket endpoint for real-time document collaboration.
    
    Protocol:
    - Client sends JSON: {"type": "sync", "data": "<base64 encoded Yjs update>"}
    - Client sends JSON: {"type": "awareness", "data": {...cursor info...}}
    - Server broadcasts to all other connections in the same document
    """
    # Authenticate
    user_id = decode_access_token(token)
    if not user_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Verify document exists
    async with async_session_factory() as db:
        result = await db.execute(
            select(Document).where(Document.edit_key == edit_key)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            await websocket.close(code=4004, reason="Document not found")
            return

        # Check permission
        perm_result = await db.execute(
            select(DocumentPermission).where(
                DocumentPermission.document_id == doc.id,
                DocumentPermission.user_id == user_id,
            )
        )
        perm = perm_result.scalar_one_or_none()
        is_creator = str(doc.creator_id) == user_id

        # Check editor limit
        editor_count = await get_active_editor_count(edit_key)
        if editor_count >= settings.MAX_EDITORS_PER_DOCUMENT and not is_creator:
            await websocket.accept()
            await websocket.send_json({
                "type": "room_full",
                "message": "Document has reached max editors",
            })
            await websocket.close(code=4029, reason="Room full")
            return

        # If user has no permission and isn't creator, they need approval
        if not perm and not is_creator:
            moniker = generate_moniker()
            await websocket.accept()
            await websocket.send_json({
                "type": "pending_approval",
                "moniker": moniker,
            })
            # Notify creator
            await add_pending_editor(edit_key, user_id, moniker)
            if edit_key in creator_connections:
                try:
                    await creator_connections[edit_key].send_json({
                        "type": "approval_request",
                        "user_id": user_id,
                        "moniker": moniker,
                    })
                except Exception:
                    pass

            # Wait for approval (poll Redis)
            approved = False
            for _ in range(120):  # 2 minute timeout
                await asyncio.sleep(1)
                async with async_session_factory() as check_db:
                    check = await check_db.execute(
                        select(DocumentPermission).where(
                            DocumentPermission.document_id == doc.id,
                            DocumentPermission.user_id == user_id,
                        )
                    )
                    if check.scalar_one_or_none():
                        approved = True
                        break

            if not approved:
                await websocket.send_json({"type": "approval_denied"})
                await websocket.close(code=4003, reason="Not approved")
                return

            await websocket.send_json({"type": "approved", "moniker": moniker})
            perm_moniker = moniker
            perm_color = generate_cursor_color()
        else:
            await websocket.accept()
            perm_moniker = perm.moniker if perm else generate_moniker()
            perm_color = perm.cursor_color if perm else generate_cursor_color()

    # Register connection
    await increment_editor_count(edit_key)
    if edit_key not in document_connections:
        document_connections[edit_key] = set()
    document_connections[edit_key].add(websocket)

    if is_creator:
        creator_connections[edit_key] = websocket

    # Send initial state
    await websocket.send_json({
        "type": "connected",
        "moniker": perm_moniker,
        "color": perm_color,
        "user_id": user_id,
    })

    # Send cached CRDT state if available, falling back to persisted state.
    cached = await get_cached_crdt_state(edit_key)
    if not cached and doc.crdt_state:
        cached = doc.crdt_state
        await cache_crdt_state(edit_key, cached)
    if cached:
        import base64
        await websocket.send_json({
            "type": "sync_state",
            "data": base64.b64encode(cached).decode(),
        })
    await websocket.send_json({"type": "sync_ready"})
    await broadcast(edit_key, websocket, json.dumps({"type": "awareness_request"}))

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "sync_update":
                # Binary CRDT update (base64 encoded)
                import base64
                update_data = base64.b64decode(msg["data"])
                await cache_crdt_state(edit_key, update_data)
                # Broadcast to all other connections
                await broadcast(edit_key, websocket, raw)

            elif msg_type == "awareness":
                # Cursor position / awareness update
                await broadcast(edit_key, websocket, raw)

            elif msg_type == "approve_editor":
                # Creator approving an editor
                if is_creator:
                    target_id = msg.get("user_id")
                    async with async_session_factory() as db:
                        new_perm = DocumentPermission(
                            document_id=doc.id,
                            user_id=target_id,
                            role="editor",
                            moniker=generate_moniker(),
                            cursor_color=generate_cursor_color(),
                        )
                        db.add(new_perm)
                        await db.commit()
                    await remove_pending_editor(edit_key, target_id)

            elif msg_type == "deny_editor":
                if is_creator:
                    target_id = msg.get("user_id")
                    await remove_pending_editor(edit_key, target_id)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Cleanup
        document_connections.get(edit_key, set()).discard(websocket)
        if not document_connections.get(edit_key):
            document_connections.pop(edit_key, None)
        if creator_connections.get(edit_key) is websocket:
            creator_connections.pop(edit_key, None)
        count = await decrement_editor_count(edit_key)
        # If last editor left, flush to postgres
        if count == 0:
            await flush_to_postgres(edit_key)


async def broadcast(edit_key: str, sender: WebSocket, message: str):
    """Broadcast a message to all connections except the sender."""
    connections = document_connections.get(edit_key, set()).copy()
    recipients = [ws for ws in connections if ws is not sender]
    results = await asyncio.gather(
        *(ws.send_text(message) for ws in recipients),
        return_exceptions=True,
    )
    for ws, result in zip(recipients, results):
        if not isinstance(result, Exception):
            continue
        document_connections.get(edit_key, set()).discard(ws)


async def flush_to_postgres(edit_key: str):
    """Flush the cached CRDT state from Redis to PostgreSQL."""
    cached = await get_cached_crdt_state(edit_key)
    if not cached:
        return
    async with async_session_factory() as db:
        result = await db.execute(
            select(Document).where(Document.edit_key == edit_key)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.crdt_state = cached
            await db.commit()
            logger.info(f"Flushed CRDT state for {edit_key} to Postgres")
