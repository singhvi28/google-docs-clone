"""
FastAPI application factory.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import engine, Base
from app.services.redis_service import (
    close_redis,
    get_dirty_documents,
    sweep_stale_editor_sessions,
)
from app.routes import auth, documents, collab, viewer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


async def _periodic_persist_loop() -> None:
    """Persist dirty CRDT logs to Postgres on a fixed interval."""
    from app.routes.collab import persist_crdt_to_postgres

    while True:
        await asyncio.sleep(settings.FLUSH_INTERVAL_SECONDS)
        try:
            dirty = await get_dirty_documents()
            for edit_key in dirty:
                try:
                    await persist_crdt_to_postgres(edit_key)
                except Exception:
                    logger.exception("Periodic persist failed for %s", edit_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic persist loop error")


async def _session_sweep_loop() -> None:
    """Expire zombie editor sessions and flush docs that become empty."""
    from app.routes.collab import flush_to_postgres

    while True:
        await asyncio.sleep(settings.SESSION_SWEEP_INTERVAL_SECONDS)
        try:
            empty_keys = await sweep_stale_editor_sessions()
            for edit_key in empty_keys:
                try:
                    await flush_to_postgres(edit_key)
                except Exception:
                    logger.exception("Sweep flush failed for %s", edit_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Session sweep loop error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    logger.info("Starting up — creating database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready.")

    persist_task = asyncio.create_task(_periodic_persist_loop())
    sweep_task = asyncio.create_task(_session_sweep_loop())
    try:
        yield
    finally:
        for task in (persist_task, sweep_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("Shutting down — closing Redis...")
        await close_redis()
        await engine.dispose()
        logger.info("Shutdown complete.")


app = FastAPI(
    title="Collaborative Editor API",
    description="Real-time collaborative document editing backend",
    version="0.1.0",
    lifespan=lifespan,
)

# ─── Middleware ────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session middleware for OAuth state
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.JWT_SECRET,
)

# ─── Routes ──────────────────────────────────────────
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(collab.router)
app.include_router(viewer.router)


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "collaborative-editor"}
