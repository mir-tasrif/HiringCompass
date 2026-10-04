"""Central configuration manager: every setting comes from environment variables (N7, D4)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder values that must never be used in production.
_INSECURE_DEFAULTS = {"change_me", "change_me_to_a_long_random_string", "change_me_min_32_chars_long_secret"}


# Typed settings object loaded from process env first, then an optional .env file.
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    # --- Application ---
    app_env: Literal["development", "production"] = "development"
    app_name: str = "HiringCompass"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: str = "http://localhost:5173"
    api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:5173"

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_dir: Path = Path("/app/logs")
    error_dir: Path = Path("/app/errors")
    log_rotate_mb: int = Field(default=10, ge=1)
    log_backup_count: int = Field(default=10, ge=1)

    # --- PostgreSQL ---
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "hiringcompass"
    postgres_user: str = "hc_user"
    postgres_password: str = "change_me"
    database_url: str = "postgresql+psycopg://hc_user:change_me@db:5432/hiringcompass"
    checkpoint_db_url: str = "postgresql://hc_user:change_me@db:5432/hiringcompass"

    # --- File storage ---
    file_storage_dir: Path = Path("/app/storage")
    export_dir: Path = Path("/app/storage/exports")
    quarantine_dir: Path = Path("/app/storage/quarantine")
    max_upload_mb: int = Field(default=10, ge=1)
    max_pdf_pages: int = Field(default=15, ge=1)
    allowed_upload_types: str = "application/pdf"

    # --- Auth ---
    jwt_secret: str = "change_me_to_a_long_random_string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(default=480, ge=1)
    invite_token_ttl_hours: int = Field(default=72, ge=1)
    seed_interviewer_email: str = "interviewer@example.com"
    seed_interviewer_password: str = "change_me"

    # --- LLM / embeddings / RAG ---
    llm_provider: Literal["ollama"] = "ollama"
    ollama_base_url: str = "http://ollama:11434"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_temperature: float = Field(default=0.0, ge=0, le=1)
    llm_seed: int = 42
    llm_timeout_seconds: int = Field(default=120, ge=1)
    llm_max_retries: int = Field(default=3, ge=0)
    llm_max_concurrency: int = Field(default=2, ge=1)
    embedding_provider: Literal["ollama"] = "ollama"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    rag_top_k: int = Field(default=5, ge=1)
    rag_namespace_company: str = "company_hiring"
    rag_namespace_technical: str = "technical_docs"
    rag_corpus_dir: Path = Path("/app/rag/corpora")

    # --- Speech-to-text (switchable) ---
    stt_provider: Literal["faster_whisper", "google"] = "faster_whisper"
    stt_language: str = "en-US"
    google_application_credentials: str = "/run/secrets/google_stt.json"
    google_stt_model: str = "latest_long"
    google_stt_stream_rotate_seconds: int = Field(default=270, ge=30)
    whisper_model_size: str = "small"
    whisper_device: Literal["cpu", "cuda"] = "cpu"
    stt_sample_rate: int = 16000

    # --- Real-time video ---
    rtc_provider: Literal["livekit"] = "livekit"
    livekit_url: str = "ws://livekit:7880"
    livekit_public_url: str = "ws://localhost:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "change_me_min_32_chars_long_secret"
    reconnect_grace_seconds: int = Field(default=60, ge=1)

    # --- Invitations ---
    invite_delivery: Literal["smtp", "manual"] = "smtp"
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "hiring@hiringcompass.local"

    # --- Worker ---
    worker_id: str = "worker-1"
    worker_poll_seconds: float = Field(default=2, gt=0)
    worker_lease_seconds: int = Field(default=120, ge=10)
    worker_max_concurrency: int = Field(default=4, ge=1)
    worker_max_attempts: int = Field(default=3, ge=1)
    worker_backoff_base_seconds: int = Field(default=5, ge=1)

    # --- Domain thresholds ---
    cv_batch_target_seconds: int = 300
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    tesseract_cmd: str = "/usr/bin/tesseract"
    integrity_policy_version: str = "1.0"
    integrity_min_font_pt: float = 4

    # Reject placeholder secrets when running in production.
    @model_validator(mode="after")
    def _reject_insecure_production(self) -> "Settings":
        if self.app_env == "production":
            bad = [n for n in ("jwt_secret", "postgres_password", "livekit_api_secret") if getattr(self, n) in _INSECURE_DEFAULTS]
            if bad:
                raise ValueError(f"insecure placeholder values in production: {', '.join(bad)}")
        return self

    # CORS origins parsed from the comma-separated env value.
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # Allowed MIME types parsed from the comma-separated env value.
    @property
    def allowed_upload_type_list(self) -> list[str]:
        return [t.strip() for t in self.allowed_upload_types.split(",") if t.strip()]

    # Maximum upload size in bytes.
    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    # Create every runtime directory the app writes to.
    def ensure_directories(self) -> None:
        for path in (self.log_dir, self.error_dir, self.file_storage_dir, self.export_dir, self.quarantine_dir):
            path.mkdir(parents=True, exist_ok=True)


# Cached accessor so the whole process shares one validated Settings instance.
@lru_cache
def get_settings() -> Settings:
    return Settings()