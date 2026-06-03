from pydantic import BaseModel, ConfigDict


class HourlyUnits(BaseModel):
    model_config = ConfigDict(extra="ignore")

    time: str
    temperature_2m: str
    showers: str
    windspeed_10m: str
    winddirection_10m: str
    windgusts_10m: str


class HourlyData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    time: list[str]
    temperature_2m: list[float]
    showers: list[float]
    windspeed_10m: list[float]
    winddirection_10m: list[float | int]
    windgusts_10m: list[float]


class WeatherResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    hourly_units: HourlyUnits
    hourly: HourlyData
