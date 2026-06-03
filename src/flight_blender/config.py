from pydantic_settings import BaseSettings, SettingsConfigDict


class FlightBlenderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./flight_blender.sqlite3"
    REDIS_BROKER_URL: str = "redis://localhost:6379/"
    SECRET_KEY: str = "changeme"
    BYPASS_AUTH_TOKEN_VERIFICATION: bool = False
    PASSPORT_AUDIENCE: str = "testflight.flightblender.com"
    PASSPORT_URL: str = "http://localhost:9000"
    PASSPORT_JWKS_URL: str = "http://localhost:9000/.well-known/jwks.json"
    DSS_AUTH_JWKS_ENDPOINT: str = "http://localhost:9000/.well-known/jwks.json"
    USSP_NETWORK_ENABLED: bool = False
    IS_DEBUG: bool = False
    FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE: str = ""
    FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER: str = ""
    FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR: str = ""
    WEATHER_API_BASE_URL: str = "https://api.open-meteo.com/v1/forecast"


settings = FlightBlenderSettings()
