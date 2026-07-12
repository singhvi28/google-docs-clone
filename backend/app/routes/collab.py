"""
Collaboration WebSocket endpoint for Yjs CRDT synchronization.

Stateless design: each server instance publishes outbound messages to Redis
Pub/Sub and listens on the same channel to forward updates to local sockets.
CRDT updates are stored as an append-only Redis log and merged on flush/join.
"""
import asyncio
import base64
import logging
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select

from app.database import async_session_factory
from app.models import Document, DocumentPermission
from app.routes.auth import decode_access_token
from app.services.awareness_batcher import enqueue_awareness, expand_awareness_messages
from app.services.redis_service import (
    get_active_editor_count,
    register_editor_session,
    unregister_editor_session,
    heartbeat_editor_session,
    append_crdt_update,
    get_merged_crdt_state,
    get_crdt_updates,
    clear_crdt_updates,
    clear_document_dirty,
    cache_crdt_state,
    merge_crdt_updates,
    seed_crdt_state_if_empty,
    add_pending_editor,
    remove_pending_editor,
    publish_update,
    subscribe_to_document,
)
from app.utils import generate_moniker, generate_cursor_color
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter(tags=["collaboration"])


async def redis_listener(pubsub, websocket: WebSocket, connection_id: str):
    """Forward Redis Pub/Sub messages to this WebSocket, skipping our own echoes."""
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = message["data"]
            text = data.decode() if isinstance(data, bytes) else data
            try:
                parsed = json.loads(text)
                if parsed.get("type") in ("awareness", "awareness_batch"):
                    for frame in expand_awareness_messages(parsed, connection_id):
                        await websocket.send_text(json.dumps(frame))
                    continue
                if parsed.get("_sid") == connection_id:
                    continue
                parsed.pop("_sid", None)
                await websocket.send_text(json.dumps(parsed))
            except Exception:
                await websocket.send_text(text)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def _publish(edit_key: str, payload: dict, connection_id: str) -> None:
    """Publish a JSON payload to the document channel, tagged with sender id."""
    envelope = {**payload, "_sid": connection_id}
    await publish_update(edit_key, json.dumps(envelope).encode())


async def _session_heartbeat_loop(edit_key: str, connection_id: str) -> None:
    """Refresh Redis session TTL so ungraceful disconnects expire cleanly."""
    interval = settings.SESSION_HEARTBEAT_INTERVAL_SECONDS
    try:
        while True:
            await asyncio.sleep(interval)
            alive = await heartbeat_editor_session(edit_key, connection_id)
            if not alive:
                return
    except asyncio.CancelledError:
        raise


@router.websocket("/ws/doc/{edit_key}")
async def document_websocket(
    websocket: WebSocket,
    edit_key: str,
    token: str = Query(default=""),
):
    """
    WebSocket endpoint for real-time document collaboration.

    Protocol:
    - Client sends JSON: {"type": "sync_update", "data": "<base64 Yjs update>"}
    - Client sends JSON: {"type": "awareness", "data": "<base64 awareness>"}
    - Server appends CRDT deltas to Redis and publishes via Pub/Sub
    """
    user_id = decode_access_token(token)
    if not user_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    async with async_session_factory() as db:
        result = await db.execute(
            select(Document).where(Document.edit_key == edit_key)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            await websocket.close(code=4004, reason="Document not found")
            return

        perm_result = await db.execute(
            select(DocumentPermission).where(
                DocumentPermission.document_id == doc.id,
                DocumentPermission.user_id == user_id,
            )
        )
        perm = perm_result.scalar_one_or_none()
        is_creator = str(doc.creator_id) == user_id
        persisted_state = doc.crdt_state
        doc_id = doc.id

        editor_count = await get_active_editor_count(edit_key)
        if editor_count >= settings.MAX_EDITORS_PER_DOCUMENT and not is_creator:
            await websocket.accept()
            await websocket.send_json({
                "type": "room_full",
                "message": "Document has reached max editors",
            })
            await websocket.close(code=4029, reason="Room full")
            return

        if not perm and not is_creator:
            moniker = generate_moniker()
            await websocket.accept()
            await websocket.send_json({
                "type": "pending_approval",
                "moniker": moniker,
            })
            await add_pending_editor(edit_key, user_id, moniker)
            # Notify creator(s) via Redis — any connected creator receives this
            await publish_update(
                edit_key,
                json.dumps({
                    "type": "approval_request",
                    "user_id": user_id,
                    "moniker": moniker,
                }).encode(),
            )

            approved = False
            for _ in range(120):
                await asyncio.sleep(1)
                async with async_session_factory() as check_db:
                    check = await check_db.execute(
                        select(DocumentPermission).where(
                            DocumentPermission.document_id == doc_id,
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

    connection_id = str(uuid.uuid4())
    await register_editor_session(edit_key, connection_id)
    heartbeat_task = asyncio.create_task(
        _session_heartbeat_loop(edit_key, connection_id)
    )

    pubsub = await subscribe_to_document(edit_key)
    listener_task = asyncio.create_task(
        redis_listener(pubsub, websocket, connection_id)
    )

    await websocket.send_json({
        "type": "connected",
        "moniker": perm_moniker,
        "color": perm_color,
        "user_id": user_id,
    })

    # Seed log from Postgres if empty, then send merged state to the new editor
    if persisted_state:
        await seed_crdt_state_if_empty(edit_key, persisted_state)

    merged = await get_merged_crdt_state(edit_key)
    if merged:
        await websocket.send_json({
            "type": "sync_state",
            "data": base64.b64encode(merged).decode(),
        })

    await websocket.send_json({"type": "sync_ready"})
    await _publish(edit_key, {"type": "awareness_request"}, connection_id)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "sync_update":
                update_data = base64.b64decode(msg["data"])
                await append_crdt_update(edit_key, update_data)
                await _publish(
                    edit_key,
                    {"type": "sync_update", "data": msg["data"]},
                    connection_id,
                )

            elif msg_type == "awareness":
                await enqueue_awareness(
                    edit_key, msg.get("data"), connection_id
                )

            elif msg_type == "approve_editor":
                if is_creator:
                    target_id = msg.get("user_id")
                    async with async_session_factory() as db:
                        new_perm = DocumentPermission(
                            document_id=doc_id,
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
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        try:
            await pubsub.unsubscribe(f"channel:{edit_key}")
            await pubsub.aclose()
        except Exception:
            pass

        count = await unregister_editor_session(edit_key, connection_id)
        if count == 0:
            await flush_to_postgres(edit_key)


async def persist_crdt_to_postgres(edit_key: str) -> None:
    """
    Merge Redis log → Postgres, then replace the log with a single baseline blob.
    Used by the periodic flush worker while editors may still be connected.
    """
    updates = await get_crdt_updates(edit_key)
    merged = await asyncio.to_thread(merge_crdt_updates, updates)
    if not merged:
        return

    async with async_session_factory() as db:
        result = await db.execute(
            select(Document).where(Document.edit_key == edit_key)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.crdt_state = merged
            await db.commit()
            logger.info(f"Persisted CRDT state for {edit_key} to Postgres")

    await cache_crdt_state(edit_key, merged)
    await clear_document_dirty(edit_key)


async def flush_to_postgres(edit_key: str):
    """Merge Redis update log into one state, persist to Postgres, truncate log."""
    updates = await get_crdt_updates(edit_key)
    merged = await asyncio.to_thread(merge_crdt_updates, updates)
    if not merged:
        await clear_document_dirty(edit_key)
        return

    async with async_session_factory() as db:
        result = await db.execute(
            select(Document).where(Document.edit_key == edit_key)
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.crdt_state = merged
            await db.commit()
            logger.info(f"Flushed CRDT state for {edit_key} to Postgres")

    await clear_crdt_updates(edit_key)
    await clear_document_dirty(edit_key)
