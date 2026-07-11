import pytest
from sqlalchemy import select

from app.models import Document
from app.routes import collab


class FakeWebSocket:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []

    async def send_text(self, message):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append(message)


@pytest.mark.asyncio
async def test_broadcast_sends_to_other_connections_and_removes_dead_sockets():
    edit_key = "edit-broadcast"
    sender = FakeWebSocket()
    receiver = FakeWebSocket()
    dead = FakeWebSocket(fail=True)
    collab.document_connections[edit_key] = {sender, receiver, dead}

    try:
        await collab.broadcast(edit_key, sender, "hello")
    finally:
        collab.document_connections.pop(edit_key, None)

    assert sender.sent == []
    assert receiver.sent == ["hello"]
    assert dead not in collab.document_connections.get(edit_key, set())


@pytest.mark.asyncio
async def test_flush_to_postgres_persists_cached_crdt_state(
    session_factory, make_user, make_document, monkeypatch
):
    creator = await make_user(email="creator@example.com")
    document = await make_document(creator=creator, edit_key="edit-flush")
    monkeypatch.setattr(collab, "async_session_factory", session_factory)
    monkeypatch.setattr(
        collab, "get_cached_crdt_state", lambda edit_key: _async_return(b"cached")
    )

    await collab.flush_to_postgres("edit-flush")

    async with session_factory() as session:
        result = await session.execute(
            select(Document).where(Document.id == document.id)
        )
        refreshed = result.scalar_one()

    assert refreshed.crdt_state == b"cached"


@pytest.mark.asyncio
async def test_flush_to_postgres_skips_when_no_cached_state(monkeypatch):
    monkeypatch.setattr(
        collab, "get_cached_crdt_state", lambda edit_key: _async_return(None)
    )

    await collab.flush_to_postgres("missing-cache")


async def _async_return(value):
    return value
