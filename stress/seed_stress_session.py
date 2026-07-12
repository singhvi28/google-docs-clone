#!/usr/bin/env python3
"""
Seed a stress-test user, JWT, and document without Google OAuth.

Usage (from repo root, with backend venv):
  backend/.venv/bin/python stress/seed_stress_session.py
  backend/.venv/bin/python stress/seed_stress_session.py --api http://localhost:8080

Writes stress/.session.env for Locust / reflector scripts.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.database import async_session_factory, engine, Base  # noqa: E402
from app.models import Document, DocumentPermission, User  # noqa: E402
from app.routes.auth import create_access_token  # noqa: E402
from app.utils import generate_cursor_color, generate_key, generate_moniker  # noqa: E402


STRESS_EMAIL = "stress-tester@example.com"
STRESS_GOOGLE_ID = "stress-google-id"


def _sample_yjs_update() -> bytes:
    from pycrdt import Doc, Text

    doc = Doc()
    doc.get("content", type=Text).insert(0, "stress")
    return bytes(doc.get_update())


async def seed(api_base: str) -> Path:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.google_id == STRESS_GOOGLE_ID))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                google_id=STRESS_GOOGLE_ID,
                email=STRESS_EMAIL,
                display_name="Stress Tester",
            )
            db.add(user)
            await db.flush()

        doc = Document(
            title="Stress Test Document",
            edit_key=generate_key(),
            view_key=generate_key(),
            creator_id=user.id,
            crdt_state=_sample_yjs_update(),
        )
        db.add(doc)
        await db.flush()
        db.add(
            DocumentPermission(
                document_id=doc.id,
                user_id=user.id,
                role="creator",
                moniker=generate_moniker(),
                cursor_color=generate_cursor_color(),
            )
        )
        await db.commit()

        token = create_access_token(str(user.id))
        sample_b64 = base64.b64encode(doc.crdt_state).decode()

        out = ROOT / "stress" / ".session.env"
        out.write_text(
            "\n".join(
                [
                    f"STRESS_API_BASE={api_base}",
                    f"STRESS_WS_BASE={api_base.replace('http://', 'ws://').replace('https://', 'wss://')}",
                    f"STRESS_TOKEN={token}",
                    f"STRESS_EDIT_KEY={doc.edit_key}",
                    f"STRESS_VIEW_KEY={doc.view_key}",
                    f"STRESS_USER_ID={user.id}",
                    f"STRESS_DOC_ID={doc.id}",
                    f"STRESS_SAMPLE_UPDATE={sample_b64}",
                    # Direct instance endpoints for reflector
                    "STRESS_INSTANCE1_WS=ws://127.0.0.1:8000",
                    "STRESS_INSTANCE2_WS=ws://127.0.0.1:8001",
                    "STRESS_INSTANCE1_WT=localhost:4433",
                    "STRESS_INSTANCE2_WT=localhost:4434",
                    "",
                ]
            )
        )
        return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed stress-test JWT + document")
    parser.add_argument(
        "--api",
        default="http://localhost:8080",
        help="API base URL written into .session.env (LB or single instance)",
    )
    args = parser.parse_args()
    path = asyncio.run(seed(args.api))
    print(f"Wrote {path}")
    print("Source it before Locust/reflector:")
    print(f"  set -a && source {path} && set +a")


if __name__ == "__main__":
    main()
