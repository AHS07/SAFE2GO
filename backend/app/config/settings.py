"""Application settings loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root, one level above backend/
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
_DEFAULT_ETA_MODEL_PATH = Path(__file__).resolve().parents[2] / "ml" / "artifacts" / "eta_model.joblib"
MIN_SECRET_LENGTH = 32
_DEFAULT_CONTENT_DIR = Path(__file__).resolve().parents[2] / "content"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        # Never echo a rejected secret in the error message.
        hide_input_in_errors=True,
    )

    app_env: Literal["dev", "demo", "test"] = "dev"
    database_url: str = "postgresql+asyncpg://safe2go:safe2go@localhost:5433/safe2go"

    # Required, no default: an empty or weak key would make every token forgeable.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480  # 8-hour shift

    credential_signing_key: str = ""
    # Offline credentials stay valid this long after the shift's scheduled end (sim time).
    offline_credential_grace_minutes: int = 60

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    sim_seed: int = 42
    sim_speed: int = 1
    sim_tick_seconds: int = 1

    eta_buffer_minutes: int = 15

    # Sync worker: poll interval and retry backoff ceiling (real seconds).
    sync_poll_seconds: float = 1.0
    sync_backoff_max_seconds: float = 60.0
    # A message failing for this long (real time, link up) moves to dead letters.
    sync_dead_letter_after_seconds: float = 3600.0

    # Telemetry streaming to Kafka for fleet analytics. Off: nothing is spooled
    # and no Kafka client starts. Safety never depends on it either way.
    kafka_enabled: bool = False
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str = "safe2go-edge"
    kafka_topic_telemetry: str = "safe2go.telemetry.raw"
    kafka_topic_events: str = "safe2go.safety.events"
    kafka_topic_partitions: int = 3
    kafka_retention_hours: int = 168
    kafka_consumer_group: str = "safe2go-cloud-analytics"
    # Forwarder: records per batch, replay ceiling (records per real second),
    # pause between empty passes, and retry backoff ceiling (real seconds).
    stream_batch_size: int = 500
    stream_max_rate: float = 2000.0
    stream_poll_seconds: float = 1.0
    stream_backoff_max_seconds: float = 30.0
    # Kafka must acknowledge a batch within this many real seconds.
    stream_send_timeout_seconds: float = 15.0
    # Spool limit (raw ticks). Above it the oldest unsent ticks are dropped
    # down to the low-water share, and a gap record reports each drop.
    stream_spool_max_records: int = 250_000
    stream_spool_low_water: float = 0.9
    eta_model_path: Path = _DEFAULT_ETA_MODEL_PATH
    content_dir: Path = _DEFAULT_CONTENT_DIR

    @field_validator("jwt_secret", "credential_signing_key")
    @classmethod
    def _require_strong_secret(cls, value: str, info) -> str:  # noqa: ANN001 - pydantic ValidationInfo
        if len(value) < MIN_SECRET_LENGTH:
            name = info.field_name.upper()
            raise ValueError(
                f"{name} must be set in .env to at least {MIN_SECRET_LENGTH} characters. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return value

    @property
    def demo_routes_enabled(self) -> bool:
        """System and simulator routes are only available in dev or demo mode."""
        return self.app_env in ("dev", "demo")

    @property
    def deepseek_enabled(self) -> bool:
        return bool(self.deepseek_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
