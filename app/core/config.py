"""Application settings, loaded from environment variables / `.env`."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "PromptDox API"
    app_version: str = "0.1.0"
    app_env: str = "development"
    port: int = 4000
    cors_origin: str = "http://localhost:3000"
    frontend_url: str | None = None

    # Database: set DATABASE_URL, or the POSTGRES_* parts and the URL is built from them.
    database_url: str | None = None
    postgres_user: str = "promptdox"
    postgres_password: str = ""
    postgres_db: str = "promptdox"
    postgres_host: str = "db"
    postgres_port: int = 5432

    # Auth
    jwt_secret_key: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    password_reset_expire_minutes: int = 60

    # Google Sign-In
    google_client_id: str = ""

    # Email
    email_provider: str = "smtp"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_pass: str = ""
    email_from: str = ""

    # AI & RAG
    gemini_api_key: str = ""
    embedding_model: str = "models/text-embedding-004"
    embedding_dimensions: int = 768
    chat_model: str = "gemini-2.5-flash"
    chunk_size: int = 600
    chunk_overlap: int = 100
    top_k_results: int = 4
    embedding_batch_size: int = 50
    ai_timeout_seconds: int = 90
    # Minimum cosine similarity for a passage to count as relevant (strict citations).
    min_relevance: float = 0.45

    # Storage
    upload_dir: Path = Path("./uploads")

    # Limits (API_CONTRACT.md section 4.8)
    max_upload_bytes: int = 50 * 1024 * 1024
    max_pages: int = 500
    max_question_chars: int = 2000
    demo_max_uploads: int = 3
    demo_max_upload_bytes: int = 5 * 1024 * 1024
    demo_daily_queries: int = 20
    demo_ttl_hours: int = 24

    @field_validator("chunk_overlap")
    @classmethod
    def _overlap_smaller_than_chunk(cls, value: int, info: ValidationInfo) -> int:
        if value >= info.data.get("chunk_size", 600):
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return value

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origin.split(",") if origin.strip()]

    @property
    def frontend_base_url(self) -> str:
        return (self.frontend_url or self.cors_origins[0]).rstrip("/")

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local"}

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_pass)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
