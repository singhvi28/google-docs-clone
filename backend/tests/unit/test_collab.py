import json

import pytest
from sqlalchemy import select

from app.models import Document
from app.routes import collab


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, message):
        self.sent.append(message)


class FakePubSub:
    def __init__(self, messages):
        self._messages = list(messages)
        self.unsubscribed = False
        self.closed = False

    async def listen(self):
        for message in self._messages:
            yield message
        # Hang until cancelled (simulates an open subscription)
        import asyncio
        await asyncio.sleep(3600)

    async def unsubscribe(self, channel):
        self.unsubscribed = True

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_redis_listener_forwards_messages_and_skips_echo():
    ws = FakeWebSocket()
    connection_id = "conn-1"
    pubsub = FakePubSub([
        {"type": "subscribe", "data": b"ok"},
        {"type": "message", "data": json.dumps({"type": "awareness", "_sid": "conn-1", "data": "x"}).encode()},
        {"type": "message", "data": json.dumps({"type": "sync_update", "_sid": "other", "data": "abc"}).encode()},
    ])

    import asyncio
    task = asyncio.create_task(collab.redis_listener(pubsub, ws, connection_id))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0]) == {"type": "sync_update", "data": "abc"}


@pytest.mark.asyncio
async def test_redis_listener_expands_awareness_batch():
    ws = FakeWebSocket()
    connection_id = "conn-1"
    batch = {
        "type": "awareness_batch",
        "cursors": [
            {"data": "mine", "_sid": "conn-1"},
            {"data": "theirs", "_sid": "conn-2"},
        ],
    }
    pubsub = FakePubSub([
        {"type": "message", "data": json.dumps(batch).encode()},
    ])

    import asyncio
    task = asyncio.create_task(collab.redis_listener(pubsub, ws, connection_id))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0]) == {"type": "awareness", "data": "theirs"}


@pytest.mark.asyncio
async def test_flush_to_postgres_merges_updates_and_clears_log(
    session_factory, make_user, make_document, monkeypatch
):
    from pycrdt import Doc, Text

    creator = await make_user(email="creator@example.com")
    document = await make_document(creator=creator, edit_key="edit-flush")
    monkeypatch.setattr(collab, "async_session_factory", session_factory)

    d = Doc()
    d.get("content", type=Text).insert(0, "persisted")
    update = d.get_update()

    cleared = {"done": False}
    dirty_cleared = {"done": False}

    async def fake_get_updates(edit_key):
        assert edit_key == "edit-flush"
        return [update]

    async def fake_clear(edit_key):
        cleared["done"] = True

    async def fake_clear_dirty(edit_key):
        dirty_cleared["done"] = True

    monkeypatch.setattr(collab, "get_crdt_updates", fake_get_updates)
    monkeypatch.setattr(collab, "clear_crdt_updates", fake_clear)
    monkeypatch.setattr(collab, "clear_document_dirty", fake_clear_dirty)

    await collab.flush_to_postgres("edit-flush")

    async with session_factory() as session:
        result = await session.execute(
            select(Document).where(Document.id == document.id)
        )
        refreshed = result.scalar_one()

    assert refreshed.crdt_state is not None
    assert cleared["done"] is True
    assert dirty_cleared["done"] is True

    out = Doc()
    out.apply_update(refreshed.crdt_state)
    assert str(out.get("content", type=Text)) == "persisted"


@pytest.mark.asyncio
async def test_persist_crdt_to_postgres_keeps_baseline_in_redis(
    session_factory, make_user, make_document, monkeypatch
):
    from pycrdt import Doc, Text

    creator = await make_user(email="creator2@example.com")
    document = await make_document(creator=creator, edit_key="edit-persist")
    monkeypatch.setattr(collab, "async_session_factory", session_factory)

    d = Doc()
    d.get("content", type=Text).insert(0, "live")
    update = d.get_update()

    cached = {}

    async def fake_get_updates(edit_key):
        return [update]

    async def fake_cache(edit_key, state):
        cached["key"] = edit_key
        cached["state"] = state

    async def fake_clear_dirty(edit_key):
        cached["dirty_cleared"] = edit_key

    monkeypatch.setattr(collab, "get_crdt_updates", fake_get_updates)
    monkeypatch.setattr(collab, "cache_crdt_state", fake_cache)
    monkeypatch.setattr(collab, "clear_document_dirty", fake_clear_dirty)

    await collab.persist_crdt_to_postgres("edit-persist")

    async with session_factory() as session:
        result = await session.execute(
            select(Document).where(Document.id == document.id)
        )
        refreshed = result.scalar_one()

    assert refreshed.crdt_state is not None
    assert cached["key"] == "edit-persist"
    assert cached["state"] == refreshed.crdt_state
    assert cached["dirty_cleared"] == "edit-persist"

    out = Doc()
    out.apply_update(refreshed.crdt_state)
    assert str(out.get("content", type=Text)) == "live"


@pytest.mark.asyncio
async def test_flush_to_postgres_skips_when_no_updates(monkeypatch):
    dirty = {"cleared": False}

    async def fake_clear_dirty(edit_key):
        dirty["cleared"] = True

    monkeypatch.setattr(
        collab, "get_crdt_updates", lambda edit_key: _async_return([])
    )
    monkeypatch.setattr(collab, "clear_document_dirty", fake_clear_dirty)

    await collab.flush_to_postgres("missing-cache")
    assert dirty["cleared"] is True


async def _async_return(value):
    return value
