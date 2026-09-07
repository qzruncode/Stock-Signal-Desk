"""Service-owned configuration. Business/LLM configuration is never imported."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_DATA_", env_file=".env.market-data", extra="ignore"
    )

    database_url: str = "sqlite:///./data/market_data.db"
    broker_url: str = "redis://127.0.0.1:6381/0"
    api_token: str = ""
    host: str = "127.0.0.1"
    port: int = 8010
    environment: str = "development"
    provider: str = "live"
    concurrency: int = Field(default=4, ge=1, le=16)
    request_timeout: float = Field(default=25, ge=1, le=120)
    scheduler_seconds: int = Field(default=30, ge=5, le=300)
    lease_seconds: int = Field(default=120, ge=30, le=900)
    rsshub_url: str = "http://127.0.0.1:1200"
    fixture_path: str = ""
    event_batch_seconds: float = Field(default=1, ge=0.1, le=5)
    event_stream_length: int = Field(default=10000, ge=1000, le=1000000)

    @model_validator(mode="after")
    def validate_deployment(self):
        if self.environment == "production":
            if not self.database_url.startswith("postgresql"):
                raise ValueError(
                    "Production market data requires its own PostgreSQL database"
                )
            if len(self.api_token) < 32:
                raise ValueError(
                    "Production market data requires an API token of at least 32 characters"
                )
            if self.provider != "live":
                raise ValueError("Fixture providers are forbidden in production")
        if self.provider not in {"live", "fixture"}:
            raise ValueError("Unknown market data provider")
        if self.provider == "fixture" and not self.fixture_path:
            raise ValueError("Fixture mode requires an explicit fixture path")
        if self.database_url.startswith("sqlite:///"):
            path = self.database_url.removeprefix("sqlite:///")
            if path != ":memory:":
                Path(path).expanduser().resolve().parent.mkdir(
                    parents=True, exist_ok=True
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
