from pydantic_settings import BaseSettings, SettingsConfigDict


class FlightBlenderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Core ───────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./flight_blender.sqlite3"
    REDIS_BROKER_URL: str = "redis://localhost:6379/"
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str | None = None
    AIR_TRAFFIC_STREAM_TTL_MS: int = 4000
    SECRET_KEY: str = "changeme"
    IS_DEBUG: bool = False
    DISABLE_JSON_LOGGING: bool = False
    AMQP_URL: str = ""
    AMQP_RECREATE_MISMATCHED_EXCHANGE: bool = False

    # ── Auth ───────────────────────────────────────────────────────────────
    BYPASS_AUTH_TOKEN_VERIFICATION: bool = False
    PASSPORT_AUDIENCE: str = "testflight.flightblender.com"
    PASSPORT_URL: str = "http://localhost:9000"
    PASSPORT_JWKS_URL: str = "http://localhost:9000/.well-known/jwks.json"
    IETF_SIGNING_KEY: str = ""
    IETF_SIGNING_KEY_ID: str = "temp_id"
    IETF_SIGNING_KEY_LABEL: str = "temp_label"
    FLIGHT_PASSPORT_SIGNING_URL: str | None = None
    FLIGHT_PASSPORT_SIGNING_CLIENT_ID: str | None = None
    FLIGHT_PASSPORT_SIGNING_CLIENT_SECRET: str | None = None
    DSS_AUTH_URL: str = "http://host.docker.internal:8085"
    DSS_AUTH_TOKEN_ENDPOINT: str = "/auth/token"
    DSS_AUTH_JWKS_ENDPOINT: str = "http://localhost:9000/.well-known/jwks.json"
    AUTH_DSS_CLIENT_ID: str | None = None
    AUTH_DSS_CLIENT_SECRET: str | None = None
    FLIGHTBLENDER_READ_SCOPE: str = "flightblender.read"
    FLIGHTBLENDER_WRITE_SCOPE: str = "flightblender.write"

    # ── DSS ────────────────────────────────────────────────────────────────
    USSP_NETWORK_ENABLED: bool = False
    DSS_BASE_URL: str = "0"
    DSS_SELF_AUDIENCE: str = "0"
    FLIGHTBLENDER_FQDN: str = "http://flight-blender:8000"
    UTM_ZONE: str = "54N"
    AUTO_SUBMIT_TO_DSS: bool = True

    # ── Surveillance / heartbeat ───────────────────────────────────────────
    HEARTBEAT_RATE_SECS: int = 5
    HEARTBEAT_MAX_LATENCY_SECS: float = 1.5
    HEARTBEAT_RETENTION_DAYS: int = 30
    ENABLE_CONFORMANCE_MONITORING: bool = False

    # ── Plugins ────────────────────────────────────────────────────────────
    FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE: str = (
        "flight_blender.infrastructure.flight_declarations.deconfliction_engine.DefaultDeconflictionEngine"
    )
    FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER: str = ""
    FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR: str = ""

    # ── External services ──────────────────────────────────────────────────
    WEATHER_API_BASE_URL: str = "https://api.open-meteo.com/v1/forecast"
    OPENSKY_NETWORK_USERNAME: str = "opensky"
    OPENSKY_NETWORK_PASSWORD: str = "opensky"

    # ── UAV defaults ───────────────────────────────────────────────────────
    DEFAULT_UAV_SPEED_M_PER_S: float = 5.5
    DEFAULT_UAV_CLIMB_RATE_M_PER_S: float = 2.0
    DEFAULT_UAV_DESCENT_RATE_M_PER_S: float = 2.0


settings = FlightBlenderSettings()
