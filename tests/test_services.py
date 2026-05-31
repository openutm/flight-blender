"""
Unit tests for service layer: deconfliction, volume_generator, weather_service,
traffic_data_fuser, and plugin_loader.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flight_blender.services.deconfliction import (
    DefaultDeconflictionEngine,
    DeconflictionRequest,
    DeconflictionResult,
)
from flight_blender.services.volume_generator import DefaultVolume4DGenerator, Volume4D


# ══════════════════════════════════════════════════════════════════════════════
# Deconfliction engine
# ══════════════════════════════════════════════════════════════════════════════


class TestDefaultDeconflictionEngine:
    def setup_method(self):
        self.engine = DefaultDeconflictionEngine()

    def test_check_deconfliction_returns_approved(self):
        req = DeconflictionRequest(
            declaration_id="test-id",
            start_datetime="2024-01-01T00:00:00",
            end_datetime="2024-01-01T01:00:00",
        )
        result = self.engine.check_deconfliction(req)
        assert isinstance(result, DeconflictionResult)
        assert result.is_approved is True
        assert result.declaration_state == 1

    def test_check_deconfliction_empty_request(self):
        req = DeconflictionRequest()
        result = self.engine.check_deconfliction(req)
        assert result.is_approved is True
        assert result.all_relevant_fences == []
        assert result.all_relevant_declarations == []

    def test_check_deconfliction_with_geojson(self):
        req = DeconflictionRequest(
            declaration_id="geo-id",
            flight_declaration_geo_json={"type": "FeatureCollection", "features": []},
        )
        result = self.engine.check_deconfliction(req)
        assert result.is_approved is True

    def test_check_deconfliction_ussp_network(self):
        req = DeconflictionRequest(ussp_network_enabled=True)
        result = self.engine.check_deconfliction(req)
        assert result.is_approved is True


# ══════════════════════════════════════════════════════════════════════════════
# Volume 4D generator
# ══════════════════════════════════════════════════════════════════════════════


class TestDefaultVolume4DGenerator:
    def setup_method(self):
        self.gen = DefaultVolume4DGenerator()

    def test_build_v4d_from_empty_geojson(self):
        fc = {"type": "FeatureCollection", "features": []}
        result = self.gen.build_v4d_from_geojson(fc, "2024-01-01T00:00:00", "2024-01-01T01:00:00")
        assert result == []

    def test_build_v4d_from_single_feature(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-0.1, 51.4], [0.1, 51.4], [0.1, 51.6], [-0.1, 51.6], [-0.1, 51.4]]],
                    },
                    "properties": {},
                }
            ],
        }
        result = self.gen.build_v4d_from_geojson(fc, "2024-01-01T00:00:00", "2024-01-01T01:00:00")
        assert len(result) == 1
        assert isinstance(result[0], Volume4D)
        assert result[0].time_start["format"] == "RFC3339"
        assert result[0].time_start["value"] == "2024-01-01T00:00:00"
        assert result[0].time_end["value"] == "2024-01-01T01:00:00"

    def test_build_v4d_uses_feature_altitude(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {},
                    "properties": {
                        "min_altitude": {"meters": 50},
                        "max_altitude": {"meters": 200},
                    },
                }
            ],
        }
        result = self.gen.build_v4d_from_geojson(fc, "2024-01-01T00:00:00", "2024-01-01T01:00:00")
        assert len(result) == 1
        assert result[0].volume["altitude_lower"]["value"] == 50
        assert result[0].volume["altitude_upper"]["value"] == 200

    def test_build_v4d_from_multiple_features(self):
        fc = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {}, "properties": {}},
                {"type": "Feature", "geometry": {}, "properties": {}},
                {"type": "Feature", "geometry": {}, "properties": {}},
            ],
        }
        result = self.gen.build_v4d_from_geojson(fc, "2024-01-01T00:00:00", "2024-01-01T01:00:00")
        assert len(result) == 3

    def test_custom_speed_params(self):
        gen = DefaultVolume4DGenerator(
            default_uav_speed_m_per_s=10.0,
            default_uav_climb_rate_m_per_s=3.5,
            default_uav_descent_rate_m_per_s=1.5,
        )
        assert gen.default_uav_speed_m_per_s == 10.0
        assert gen.default_uav_climb_rate_m_per_s == 3.5
        assert gen.default_uav_descent_rate_m_per_s == 1.5


# ══════════════════════════════════════════════════════════════════════════════
# Weather service
# ══════════════════════════════════════════════════════════════════════════════


class TestWeatherService:
    def test_init(self):
        from flight_blender.services.weather_service import WeatherService

        svc = WeatherService(base_url="https://api.example.com/weather")
        assert svc.base_url == "https://api.example.com/weather"

    @pytest.mark.anyio
    async def test_get_weather_success(self):
        from flight_blender.services.weather_service import WeatherService

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "current_weather": {"temperature": 15.0},
            "hourly": {"time": [], "temperature_2m": []},
        }

        with patch("flight_blender.services.weather_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response

            svc = WeatherService(base_url="https://api.example.com/weather")
            result = await svc.get_weather(longitude=-0.1, latitude=51.5, timezone="UTC")

        assert result["current_weather"]["temperature"] == 15.0

    @pytest.mark.anyio
    async def test_get_weather_non_200_raises(self):
        from flight_blender.services.weather_service import WeatherService

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Service unavailable"

        with patch("flight_blender.services.weather_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_response

            svc = WeatherService(base_url="https://api.example.com/weather")
            with pytest.raises(ValueError, match="Error fetching weather data"):
                await svc.get_weather(longitude=-0.1, latitude=51.5, timezone="UTC")


# ══════════════════════════════════════════════════════════════════════════════
# Traffic data fuser
# ══════════════════════════════════════════════════════════════════════════════


class TestDefaultTrafficDataFuser:
    def test_generate_track_messages_empty(self):
        from flight_blender.services.traffic_data_fuser import DefaultTrafficDataFuser

        fuser = DefaultTrafficDataFuser(session_id="s1", raw_observations=[])
        assert fuser.generate_track_messages() == []

    def test_generate_track_messages_single_observation(self):
        from flight_blender.services.traffic_data_fuser import DefaultTrafficDataFuser

        obs = [{"icao_address": "ABC123", "lat_dd": 51.5, "lon_dd": -0.1, "altitude_mm": 100, "timestamp": 1000}]
        fuser = DefaultTrafficDataFuser(session_id="s1", raw_observations=obs)
        messages = fuser.generate_track_messages()
        assert len(messages) == 1
        assert messages[0].unique_aircraft_identifier == "ABC123"
        assert messages[0].state["position"]["lat"] == 51.5

    def test_generate_track_messages_deduplicates_by_icao(self):
        from flight_blender.services.traffic_data_fuser import DefaultTrafficDataFuser

        obs = [
            {"icao_address": "ABC123", "lat_dd": 51.0, "lon_dd": -0.1, "timestamp": 999},
            {"icao_address": "ABC123", "lat_dd": 52.0, "lon_dd": -0.2, "timestamp": 1000},
        ]
        fuser = DefaultTrafficDataFuser(session_id="s1", raw_observations=obs)
        messages = fuser.generate_track_messages()
        assert len(messages) == 1
        # Should keep the later observation
        assert messages[0].state["position"]["lat"] == 52.0

    def test_generate_track_messages_skips_empty_icao(self):
        from flight_blender.services.traffic_data_fuser import DefaultTrafficDataFuser

        obs = [{"icao_address": "", "lat_dd": 51.5, "lon_dd": -0.1}]
        fuser = DefaultTrafficDataFuser(session_id="s1", raw_observations=obs)
        assert fuser.generate_track_messages() == []

    def test_generate_track_messages_multiple_aircraft(self):
        from flight_blender.services.traffic_data_fuser import DefaultTrafficDataFuser

        obs = [
            {"icao_address": "ALPHA", "lat_dd": 51.0, "lon_dd": -0.1, "timestamp": 1000},
            {"icao_address": "BETA", "lat_dd": 52.0, "lon_dd": 0.0, "timestamp": 1001},
        ]
        fuser = DefaultTrafficDataFuser(session_id="s1", raw_observations=obs)
        messages = fuser.generate_track_messages()
        assert len(messages) == 2


# ══════════════════════════════════════════════════════════════════════════════
# Plugin loader
# ══════════════════════════════════════════════════════════════════════════════


class TestPluginLoader:
    def setup_method(self):
        # Clear lru_cache between tests
        from flight_blender.common.plugin_loader import load_plugin

        load_plugin.cache_clear()

    def test_load_plugin_returns_class(self):
        from flight_blender.common.plugin_loader import load_plugin
        from flight_blender.services.deconfliction import DefaultDeconflictionEngine

        cls = load_plugin("flight_blender.services.deconfliction.DefaultDeconflictionEngine")
        assert cls is DefaultDeconflictionEngine

    def test_load_plugin_with_protocol_validation(self):
        from flight_blender.common.plugin_loader import load_plugin
        from flight_blender.services.deconfliction import DeconflictionEngine

        cls = load_plugin(
            "flight_blender.services.deconfliction.DefaultDeconflictionEngine",
            expected_protocol=DeconflictionEngine,
        )
        assert cls is not None

    def test_load_plugin_invalid_path_raises(self):
        from flight_blender.common.plugin_loader import load_plugin

        with pytest.raises((ImportError, ModuleNotFoundError)):
            load_plugin("nonexistent.module.SomeClass")

    def test_load_plugin_invalid_class_raises(self):
        from flight_blender.common.plugin_loader import load_plugin

        with pytest.raises(AttributeError):
            load_plugin("flight_blender.services.deconfliction.NonExistentClass")

    def test_load_plugin_caches_result(self):
        from flight_blender.common.plugin_loader import load_plugin

        cls1 = load_plugin("flight_blender.services.volume_generator.DefaultVolume4DGenerator")
        cls2 = load_plugin("flight_blender.services.volume_generator.DefaultVolume4DGenerator")
        assert cls1 is cls2

    def test_check_protocol_raises_when_class_does_not_match_protocol(self):
        """Class that doesn't satisfy the protocol should raise TypeError."""
        from typing import Protocol, runtime_checkable

        from flight_blender.common.plugin_loader import _check_protocol

        @runtime_checkable
        class MyProtocol(Protocol):
            def required_method(self) -> str: ...

        class BadClass:
            pass  # missing required_method

        with pytest.raises(TypeError):
            _check_protocol(BadClass, "test.BadClass", MyProtocol)

    def test_check_protocol_passes_for_satisfying_class(self):
        """Class that satisfies the protocol should not raise."""
        from typing import Protocol, runtime_checkable

        from flight_blender.common.plugin_loader import _check_protocol

        @runtime_checkable
        class MyProtocol(Protocol):
            def required_method(self) -> str: ...

        class GoodClass:
            def required_method(self) -> str:
                return "ok"

        # Should not raise
        _check_protocol(GoodClass, "test.GoodClass", MyProtocol)

    def test_check_protocol_new_fails_missing_methods(self):
        """When __new__ raises TypeError and class is missing protocol members, raise TypeError."""
        from typing import Protocol, runtime_checkable

        from flight_blender.common.plugin_loader import _check_protocol

        @runtime_checkable
        class StrictProtocol(Protocol):
            def must_have_this(self) -> None: ...

        class CannotInstantiate:
            """Class whose __new__ raises TypeError (requires extra args)."""

            def __new__(cls, required_arg):  # noqa: ARG003
                return super().__new__(cls)

        with pytest.raises(TypeError, match="missing required protocol members"):
            _check_protocol(CannotInstantiate, "test.CannotInstantiate", StrictProtocol)

    def test_check_protocol_new_fails_but_methods_present(self):
        """When __new__ raises TypeError but all protocol methods exist, no raise."""
        from typing import Protocol, runtime_checkable

        from flight_blender.common.plugin_loader import _check_protocol

        @runtime_checkable
        class SimpleProtocol(Protocol):
            def present_method(self) -> None: ...

        class CannotInstantiate:
            def __new__(cls, required_arg):  # noqa: ARG003
                return super().__new__(cls)

            def present_method(self) -> None:
                pass

        # Should not raise (all methods present)
        _check_protocol(CannotInstantiate, "test.CannotInstantiate", SimpleProtocol)
