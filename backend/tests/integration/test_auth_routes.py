import pytest


@pytest.mark.asyncio
async def test_me_returns_authenticated_user(client, make_user, set_current_user):
    user = await make_user(
        email="me@example.com",
        display_name="Current User",
        avatar_url="https://example.com/avatar.png",
    )
    set_current_user(user)

    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"
    assert response.json()["display_name"] == "Current User"


@pytest.mark.asyncio
async def test_me_requires_authentication(client):
    response = await client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"
