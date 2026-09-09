"""
Application configuration via Pydantic Settings.

All configuration is loaded from environment variables / .env file.
Secrets must NEVER be hard-coded here.
"""

from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central application settings.

    Values are loaded from environment variables and .env file.
    For production, use a secure secret-management system for sensitive values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────
    APP_NAME: str = "HealthcareSaaS"
    APP_ENV: str = "development"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    API_V1_STR: str = "/api/v1"

    # ── Database ─────────────────────────────────────────────────────────
    DATABASE_URL: str

    # Connection pool
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 3600

    # ── JWT ──────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Security & CORS ──────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000"

    # ── Password Policy ──────────────────────────────────────────────────
    PASSWORD_MIN_LENGTH: int = 8
    PASSWORD_MAX_LENGTH: int = 128

    # ── File Uploads ──────────────────────────────────────────────────────
    UPLOAD_DIR: str = "uploads/doctor_documents"
    MAX_UPLOAD_SIZE_MB: int = 10
    ALLOWED_UPLOAD_TYPES: str = "application/pdf,image/jpeg,image/png"

    # ── Groq LLM Configuration ───────────────────────────────────────────
    GROQ_API: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_VISION_MODEL: Optional[str] = None
    GROQ_SCAN_MODEL: Optional[str] = None

    # ── Test Database ────────────────────────────────────────────────────
    TEST_DATABASE_URL: str = ""

    @property
    def groq_api_key(self) -> Optional[str]:
        """Return the Groq API key from GROQ_API or GROQ_API_KEY."""
        return self.GROQ_API or self.GROQ_API_KEY

    @property
    def groq_scan_model(self) -> str:
        """Return the model to use for scanned documents and vision analysis from .env (e.g. GROQ_SCAN_MODEL)."""
        if self.GROQ_SCAN_MODEL and self.GROQ_SCAN_MODEL.strip():
            return self.GROQ_SCAN_MODEL.strip()
        if self.GROQ_VISION_MODEL and self.GROQ_VISION_MODEL.strip() and "llama-3.2-11b-vision-preview" not in self.GROQ_VISION_MODEL:
            return self.GROQ_VISION_MODEL.strip()
        return "qwen/qwen3.6-27b"

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse comma-separated CORS origins into a list."""
        return [
            origin.strip()
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def allowed_upload_types_list(self) -> List[str]:
        """Parse comma-separated allowed MIME types."""
        return [
            t.strip()
            for t in self.ALLOWED_UPLOAD_TYPES.split(",")
            if t.strip()
        ]

    @property
    def max_upload_size_bytes(self) -> int:
        """Convert MB to bytes."""
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache()
def get_settings() -> Settings:
    """
    Return cached Settings instance.

    Using lru_cache ensures the .env file is read only once.
    """
    return Settings()
