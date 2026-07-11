"""
FastAPI application factory.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import engine, Base
from app.services.redis_service import close_redis
from app.routes import auth, documents, collab, viewer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    logger.info("Starting up — creating database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready.")
    yield
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
