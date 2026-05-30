"""
Application configuration using Pydantic Settings.
All settings are loaded from environment variables with sensible defaults.
"""

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Application ────────────────────────────────────────────────────────────
    app_title: str = "Flight Blender"
    app_version: str = "2.0.0"
    debug: bool = Field(default=False, alias="IS_DEBUG")
    allowed_hosts: list[str] = Field(default=["*"], alias="ALLOWED_HOSTS")

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite+aiosqlite:///./flight_blender.db",
        alias="DATABASE_URL",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        """Normalize legacy Postgres URL schemes to the asyncpg async dialect.

        Heroku/Render/etc. export ``postgres://`` or ``postgresql://`` URLs.
        SQLAlchemy 2 async requires ``postgresql+asyncpg://``.
        """
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+asyncpg://", 1)
            if v.startswith("postgresql://"):
                return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    # ── Redis ──────────────────────────────────────────────────────────────────
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_password: str | None = Field(default=None, alias="REDIS_PASSWORD")
    redis_broker_url: str = Field(default="redis://redis:6379/", alias="REDIS_BROKER_URL")

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    # ── DSS Authentication ─────────────────────────────────────────────────────
    dss_auth_url: str = Field(default="http://host.docker.internal:8085", alias="DSS_AUTH_URL")
    dss_auth_token_endpoint: str = Field(default="/auth/token", alias="DSS_AUTH_TOKEN_ENDPOINT")
    dss_self_audience: str = Field(default="localhost", alias="DSS_SELF_AUDIENCE")
    auth_dss_client_id: str = Field(default="", alias="AUTH_DSS_CLIENT_ID")
    auth_dss_client_secret: str = Field(default="", alias="AUTH_DSS_CLIENT_SECRET")

    # ── API Auth ───────────────────────────────────────────────────────────────
    bypass_auth_token_verification: bool = Field(default=False, alias="BYPASS_AUTH_TOKEN_VERIFICATION")
    auth_server_jwks_uri: str = Field(default="", alias="AUTH_SERVER_JWKS_URI")
    # Expected JWT audience (Django's PASSPORT_AUDIENCE / API_IDENTIFIER). When set,
    # inbound tokens must carry a matching ``aud`` claim or they are rejected.
    auth_audience: str = Field(default="", alias="AUTH_AUDIENCE")

    # ── Celery ─────────────────────────────────────────────────────────────────
    celery_broker_url: str = Field(default="", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="", alias="CELERY_RESULT_BACKEND")

    @model_validator(mode="after")
    def _fill_celery_urls_from_redis(self) -> "Settings":
        """Fall back to REDIS_BROKER_URL / redis_url when CELERY_* vars are absent."""
        if not self.celery_broker_url:
            self.celery_broker_url = self.redis_broker_url
        if not self.celery_result_backend:
            self.celery_result_backend = self.redis_url
        return self

    # ── Weather ────────────────────────────────────────────────────────────────
    weather_api_base_url: str = Field(default="https://api.open-meteo.com/v1/forecast", alias="WEATHER_API_BASE_URL")

    # ── Scopes ─────────────────────────────────────────────────────────────────
    flightblender_read_scope: str = "blender.read"
    flightblender_write_scope: str = "blender.write"

    # ── Plugin Engine Settings ─────────────────────────────────────────────────
    plugin_traffic_data_fuser: str = Field(
        default="flight_blender.services.traffic_data_fuser.DefaultTrafficDataFuser",
        alias="FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER",
    )
    plugin_volume_4d_generator: str = Field(
        default="flight_blender.services.volume_generator.DefaultVolume4DGenerator",
        alias="FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR",
    )
    plugin_deconfliction_engine: str = Field(
        default="flight_blender.services.deconfliction.DefaultDeconflictionEngine",
        alias="FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE",
    )

    # ── USSP / DSS ─────────────────────────────────────────────────────────────
    ussp_network_enabled: int = Field(default=0, alias="USSP_NETWORK_ENABLED")
    auto_submit_to_dss: int = Field(default=1, alias="AUTO_SUBMIT_TO_DSS")

    # ── Surveillance ───────────────────────────────────────────────────────────
    heartbeat_retention_days: int = Field(default=30, alias="HEARTBEAT_RETENTION_DAYS")
    surveillance_sdsp_name: str = Field(default="Flight Blender SDSP", alias="SURVEILLANCE_SDSP_NAME")

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def split_allowed_hosts(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [h.strip() for h in v.split(",")]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
