import pytest
from sqlalchemy import select

from app.models import Document, DocumentPermission
from app.routes import documents


@pytest.mark.asyncio
async def test_create_document_creates_document_and_creator_permission(
    client, make_user, set_current_user, session_factory, monkeypatch
):
    user = await make_user(email="creator@example.com")
    set_current_user(user)
    keys = iter(["edit-created", "view-created"])
    monkeypatch.setattr(documents, "generate_key", lambda: next(keys))
    monkeypatch.setattr(documents, "generate_moniker", lambda: "swiftphoenix")
    monkeypatch.setattr(documents, "generate_cursor_color", lambda: "#FF6B6B")

    response = await client.post("/api/documents/", json={"title": "Launch Notes"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["title"] == "Launch Notes"
    assert payload["edit_key"] == "edit-created"
    assert payload["view_key"] == "view-created"

    async with session_factory() as session:
        permission_result = await session.execute(
            select(DocumentPermission).where(
                DocumentPermission.document_id == payload["id"],
                DocumentPermission.user_id == user.id,
            )
        )
        permission = permission_result.scalar_one()

    assert permission.role == "creator"
    assert permission.moniker == "swiftphoenix"
    assert permission.cursor_color == "#FF6B6B"


@pytest.mark.asyncio
async def test_list_documents_filters_created_edited_and_all_tabs(
    client,
    make_user,
    make_document,
    make_permission,
    set_current_user,
):
    user = await make_user(email="owner@example.com")
    other = await make_user(email="other@example.com")
    created = await make_document(
        creator=user,
        title="Created Doc",
        edit_key="created-edit",
        view_key="created-view",
    )
    edited = await make_document(
        creator=other,
        title="Edited Doc",
        edit_key="edited-edit",
        view_key="edited-view",
    )
    await make_permission(document=edited, user=user, role="editor")
    set_current_user(user)

    created_response = await client.get("/api/documents/?tab=created")
    edited_response = await client.get("/api/documents/?tab=edited")
    all_response = await client.get("/api/documents/?tab=all")

    assert [doc["id"] for doc in created_response.json()] == [str(created.id)]
    assert [doc["id"] for doc in edited_response.json()] == [str(edited.id)]
    assert {doc["id"] for doc in all_response.json()} == {
        str(created.id),
        str(edited.id),
    }


@pytest.mark.asyncio
async def test_get_document_allows_creator_and_editor_but_rejects_others(
    client,
    make_user,
    make_document,
    make_permission,
    set_current_user,
):
    creator = await make_user(email="creator@example.com")
    editor = await make_user(email="editor@example.com")
    outsider = await make_user(email="outsider@example.com")
    document = await make_document(creator=creator)
    await make_permission(document=document, user=editor, role="editor")

    set_current_user(creator)
    creator_response = await client.get(f"/api/documents/{document.id}")
    set_current_user(editor)
    editor_response = await client.get(f"/api/documents/{document.id}")
    set_current_user(outsider)
    outsider_response = await client.get(f"/api/documents/{document.id}")

    assert creator_response.status_code == 200
    assert editor_response.status_code == 200
    assert outsider_response.status_code == 403
    assert outsider_response.json()["detail"] == "Access denied"


@pytest.mark.asyncio
async def test_creator_can_rename_and_delete_document(
    client, make_user, make_document, set_current_user, session_factory
):
    creator = await make_user(email="creator@example.com")
    document = await make_document(creator=creator)
    set_current_user(creator)

    rename_response = await client.patch(
        f"/api/documents/{document.id}/title",
        json={"title": "Renamed"},
    )
    delete_response = await client.delete(f"/api/documents/{document.id}")

    assert rename_response.status_code == 200
    assert rename_response.json() == {"title": "Renamed"}
    assert delete_response.status_code == 204

    async with session_factory() as session:
        result = await session.execute(select(Document).where(Document.id == document.id))

    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_non_creator_cannot_rename_or_delete_document(
    client, make_user, make_document, make_permission, set_current_user
):
    creator = await make_user(email="creator@example.com")
    editor = await make_user(email="editor@example.com")
    document = await make_document(creator=creator)
    await make_permission(document=document, user=editor, role="editor")
    set_current_user(editor)

    rename_response = await client.patch(
        f"/api/documents/{document.id}/title",
        json={"title": "Nope"},
    )
    delete_response = await client.delete(f"/api/documents/{document.id}")

    assert rename_response.status_code == 403
    assert rename_response.json()["detail"] == "Only creator can rename"
    assert delete_response.status_code == 403
    assert delete_response.json()["detail"] == "Only creator can delete"


@pytest.mark.asyncio
async def test_lookup_document_by_edit_and_view_keys(client, make_user, make_document):
    creator = await make_user(email="creator@example.com")
    document = await make_document(
        creator=creator,
        title="Shared Doc",
        edit_key="edit-lookup",
        view_key="view-lookup",
    )

    edit_response = await client.get("/api/documents/by-edit-key/edit-lookup")
    view_response = await client.get("/api/documents/by-view-key/view-lookup")

    assert edit_response.status_code == 200
    assert edit_response.json()["id"] == str(document.id)
    assert view_response.status_code == 200
    assert view_response.json()["id"] == str(document.id)


@pytest.mark.asyncio
async def test_document_lookup_routes_return_404_for_unknown_keys(client):
    edit_response = await client.get("/api/documents/by-edit-key/missing")
    view_response = await client.get("/api/documents/by-view-key/missing")

    assert edit_response.status_code == 404
    assert view_response.status_code == 404
