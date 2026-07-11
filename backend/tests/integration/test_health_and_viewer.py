import base64

import pytest

from app.routes import viewer


class FakeRequest:
    def __init__(self):
        self.calls = 0

    async def is_disconnected(self):
        self.calls += 1
        return self.calls > 1


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
async def test_viewer_sse_streams_cached_state(
    session_factory, make_user, make_document, monkeypatch
):
    creator = await make_user(email="viewer-owner@example.com")
    await make_document(
        creator=creator,
        edit_key="edit-viewer",
        view_key="view-viewer",
    )
    monkeypatch.setattr(viewer, "async_session_factory", session_factory)
    monkeypatch.setattr(viewer, "get_cached_crdt_state", lambda edit_key: _async_return(b"state"))
    monkeypatch.setattr(viewer.settings, "VIEWER_SYNC_INTERVAL_SECONDS", 0)

    response = await viewer.viewer_sse("view-viewer", FakeRequest())
    chunk = await anext(response.body_iterator)

    assert response.status_code == 200
    assert chunk == (
        f'data: {{"type":"state","data":"{base64.b64encode(b"state").decode()}"}}\n\n'
    )


@pytest.mark.asyncio
async def test_viewer_sse_streams_persisted_state_when_cache_is_empty(
    session_factory, make_user, make_document, monkeypatch
):
    cached_states = {}
    creator = await make_user(email="viewer-fallback-owner@example.com")
    document = await make_document(
        creator=creator,
        edit_key="edit-viewer-fallback",
        view_key="view-viewer-fallback",
    )
    document.crdt_state = b"persisted-state"

    async def fake_cache_crdt_state(edit_key, state):
        cached_states[edit_key] = state

    monkeypatch.setattr(viewer, "async_session_factory", session_factory)
    monkeypatch.setattr(viewer, "get_cached_crdt_state", lambda edit_key: _async_return(None))
    monkeypatch.setattr(viewer, "cache_crdt_state", fake_cache_crdt_state)
    monkeypatch.setattr(viewer.settings, "VIEWER_SYNC_INTERVAL_SECONDS", 0)

    response = await viewer.viewer_sse("view-viewer-fallback", FakeRequest())
    chunk = await anext(response.body_iterator)

    assert response.status_code == 200
    assert chunk == (
        f'data: {{"type":"state","data":"{base64.b64encode(b"persisted-state").decode()}"}}\n\n'
    )
    assert cached_states == {"edit-viewer-fallback": b"persisted-state"}


async def _async_return(value):
    return value
