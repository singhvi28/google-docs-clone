from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

from app.models import User
from app.routes import auth


@pytest.mark.asyncio
async def test_login_uses_google_callback_redirect_uri(monkeypatch):
    class FakeGoogleOAuth:
        async def authorize_redirect(self, request, redirect_uri):
            return {"request": request, "redirect_uri": redirect_uri}

    monkeypatch.setattr(auth.oauth, "google", FakeGoogleOAuth())

    response = await auth.login(request=object())

    assert response["redirect_uri"] == (
        f"{auth.settings.BACKEND_URL}/api/auth/callback/google"
    )


def test_create_and_decode_access_token_round_trip():
    user_id = str(uuid4())

    token = auth.create_access_token(user_id)

    assert auth.decode_access_token(token) == user_id


def test_decode_access_token_returns_none_for_invalid_token():
    assert auth.decode_access_token("not-a-jwt") is None


def test_decode_access_token_returns_none_for_expired_token():
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        auth.settings.JWT_SECRET,
        algorithm=auth.settings.JWT_ALGORITHM,
    )

    assert auth.decode_access_token(expired) is None


@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_credentials():
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(credentials=None, db=None)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Not authenticated"


@pytest.mark.asyncio
async def test_get_current_user_rejects_unknown_user(session_factory):
    token = auth.create_access_token(str(uuid4()))
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await auth.get_current_user(credentials=credentials, db=session)

    assert exc.value.status_code == 401
    assert exc.value.detail == "User not found"


@pytest.mark.asyncio
async def test_get_current_user_returns_matching_user(session_factory):
    user = User(
        google_id="google-auth",
        email="auth@example.com",
        display_name="Auth User",
    )
    async with session_factory() as session:
        session.add(user)
        await session.commit()
        token = auth.create_access_token(str(user.id))
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        current_user = await auth.get_current_user(credentials=credentials, db=session)

    assert current_user.id == user.id
    assert current_user.email == "auth@example.com"
