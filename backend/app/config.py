from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path


ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://gdocs:gdocs_secret@localhost:5432/gdocs_prod"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # JWT
    JWT_SECRET: str = "super-secret-dev-key-change-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 72

    # App URLs
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # Collaboration limits
    MAX_EDITORS_PER_DOCUMENT: int = 50
    FLUSH_INTERVAL_SECONDS: int = 60  # periodic Postgres persist while editors active
    CRDT_CHECKPOINT_THRESHOLD: int = 100  # compact Redis log when LLEN reaches this
    SESSION_TTL_SECONDS: int = 30
    SESSION_HEARTBEAT_INTERVAL_SECONDS: int = 10
    SESSION_SWEEP_INTERVAL_SECONDS: int = 15
    AWARENESS_BATCH_WINDOW_MS: int = 25

    # WebTransport / QUIC
    WEBTRANSPORT_PORT: int = 4433
    TLS_CERTFILE: str = "certs/cert.pem"
    TLS_KEYFILE: str = "certs/key.pem"

    model_config = {
        "env_file": ROOT_ENV_FILE,
        "env_file_encoding": "utf-8",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
