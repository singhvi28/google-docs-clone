import base64
import json

import pytest

from app.routes import viewer


class FakeRequest:
    def __init__(self, disconnect_after=1):
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self):
        self.calls += 1
        return self.calls > self.disconnect_after


class FakePubSub:
    def __init__(self, messages=None):
        self._messages = list(messages or [])
        self.unsubscribed = False
        self.closed = False

    async def listen(self):
        for message in self._messages:
            yield message

    async def unsubscribe(self, channel):
        self.unsubscribed = True

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "collaborative-editor",
    }


@pytest.mark.asyncio
async def test_viewer_sse_returns_404_stream_for_unknown_view_key(
    session_factory, monkeypatch
):
    monkeypatch.setattr(viewer, "async_session_factory", session_factory)

    response = await viewer.viewer_sse("missing", FakeRequest())

    assert response.status_code == 404
    assert response.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_viewer_sse_streams_initial_merged_state(
    session_factory, make_user, make_document, monkeypatch
):
    creator = await make_user(email="viewer-owner@example.com")
    await make_document(
        creator=creator,
        edit_key="edit-viewer",
        view_key="view-viewer",
    )
    monkeypatch.setattr(viewer, "async_session_factory", session_factory)
    monkeypatch.setattr(
        viewer, "get_merged_crdt_state", lambda edit_key: _async_return(b"state")
    )
    monkeypatch.setattr(
        viewer, "subscribe_to_document", lambda edit_key: _async_return(FakePubSub())
    )

    response = await viewer.viewer_sse("view-viewer", FakeRequest())
    chunk = await anext(response.body_iterator)

    assert response.status_code == 200
    assert chunk == (
        f'data: {{"type":"state","data":"{base64.b64encode(b"state").decode()}"}}\n\n'
    )


@pytest.mark.asyncio
async def test_viewer_sse_streams_live_sync_updates_from_pubsub(
    session_factory, make_user, make_document, monkeypatch
):
    creator = await make_user(email="viewer-live@example.com")
    await make_document(
        creator=creator,
        edit_key="edit-viewer-live",
        view_key="view-viewer-live",
    )
    monkeypatch.setattr(viewer, "async_session_factory", session_factory)
    monkeypatch.setattr(
        viewer, "get_merged_crdt_state", lambda edit_key: _async_return(None)
    )

    live_msg = json.dumps({
        "type": "sync_update",
        "data": "ZGVsdGE=",
        "_sid": "editor-1",
    }).encode()
    pubsub = FakePubSub([
        {"type": "subscribe", "data": b"ok"},
        {"type": "message", "data": json.dumps({"type": "awareness", "data": "x"}).encode()},
        {"type": "message", "data": live_msg},
    ])
    monkeypatch.setattr(
        viewer, "subscribe_to_document", lambda edit_key: _async_return(pubsub)
    )

    response = await viewer.viewer_sse(
        "view-viewer-live", FakeRequest(disconnect_after=10)
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
        if len(chunks) >= 2:
            break

    assert chunks[0] == 'data: {"type":"heartbeat"}\n\n'
    assert '"type": "sync_update"' in chunks[1] or '"type":"sync_update"' in chunks[1]
    assert "ZGVsdGE=" in chunks[1]
    assert "_sid" not in chunks[1]


@pytest.mark.asyncio
async def test_viewer_sse_streams_persisted_state_when_log_is_empty(
    session_factory, make_user, make_document, monkeypatch
):
    seeded = {}
    creator = await make_user(email="viewer-fallback-owner@example.com")
    document = await make_document(
        creator=creator,
        edit_key="edit-viewer-fallback",
        view_key="view-viewer-fallback",
    )
    document.crdt_state = b"persisted-state"

    async def fake_seed(edit_key, state):
        seeded[edit_key] = state

    monkeypatch.setattr(viewer, "async_session_factory", session_factory)
    monkeypatch.setattr(
        viewer, "get_merged_crdt_state", lambda edit_key: _async_return(None)
    )
    monkeypatch.setattr(viewer, "seed_crdt_state_if_empty", fake_seed)
    monkeypatch.setattr(
        viewer, "subscribe_to_document", lambda edit_key: _async_return(FakePubSub())
    )

    response = await viewer.viewer_sse("view-viewer-fallback", FakeRequest())
    chunk = await anext(response.body_iterator)

    assert response.status_code == 200
    assert chunk == (
        f'data: {{"type":"state","data":"{base64.b64encode(b"persisted-state").decode()}"}}\n\n'
    )
    assert seeded == {"edit-viewer-fallback": b"persisted-state"}


async def _async_return(value):
    return value
