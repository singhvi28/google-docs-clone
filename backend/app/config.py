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
    VIEWER_SYNC_INTERVAL_SECONDS: int = 3
    FLUSH_INTERVAL_SECONDS: int = 300  # 5 minutes

    model_config = {
        "env_file": ROOT_ENV_FILE,
        "env_file_encoding": "utf-8",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
