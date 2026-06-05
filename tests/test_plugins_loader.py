"""Tests for the universal plugin loader and plugin-based extension points.

Covers:
- ``load_plugin`` importing, caching, and error handling
- ``DeconflictionResult`` backward-compat alias (``IntersectionCheckResult``)
- ``DeconflictionRequest`` defaults and field assignment
- Plugin settings (new prefix, backward-compat fallback)
"""

from datetime import datetime, timezone
from unittest import TestCase

from flight_blender.domain_types.plugin_protocols import DeconflictionEngineProtocol, TrafficDataFuserProtocol as TrafficDataFuserProtocol
from flight_blender.plugins.loader import load_plugin
from flight_blender.domain_types.flight_declarations import (
    DeconflictionRequest,
    DeconflictionResult,
    IntersectionCheckResult,
)
from flight_blender.services.deconfliction_engine import DefaultDeconflictionEngine
from flight_blender.services.surveillance_svc import TrafficDataFuser
from flight_blender.plugins.examples.altitude_aware_deconfliction_engine import (
    AltitudeAwareDeconflictionEngine,
)

# ---------------------------------------------------------------------------
# load_plugin — core mechanics
# ---------------------------------------------------------------------------


class LoadPluginTests(TestCase):
    """Tests for flight_blender.plugins.loader.load_plugin."""

    def setUp(self):
        load_plugin.cache_clear()

    def tearDown(self):
        load_plugin.cache_clear()

    # -- valid path --------------------------------------------------------

    def test_valid_path_returns_correct_class(self):
        cls = load_plugin("flight_blender.services.deconfliction_engine.DefaultDeconflictionEngine")
        self.assertIs(cls, DefaultDeconflictionEngine)

    def test_valid_path_different_module(self):
        """load_plugin works across modules."""
        cls = load_plugin("flight_blender.plugins.examples.altitude_aware_deconfliction_engine.AltitudeAwareDeconflictionEngine")
        self.assertIs(cls, AltitudeAwareDeconflictionEngine)

    # -- invalid paths -----------------------------------------------------

    def test_invalid_module_raises_import_error(self):
        with self.assertRaises((ImportError, ModuleNotFoundError)):
            load_plugin("totally.fake.module.ClassName")

    def test_invalid_class_raises_attribute_error(self):
        with self.assertRaises(AttributeError):
            load_plugin("flight_blender.services.deconfliction_engine.NonExistentClass")

    def test_no_dot_in_path_raises_value_error(self):
        """A path without a dot cannot be split into module + class."""
        with self.assertRaises(ValueError):
            load_plugin("NoDotPath")

    def test_empty_string_raises(self):
        """An empty string is invalid."""
        with self.assertRaises((ValueError, ImportError)):
            load_plugin("")

    # -- protocol validation -----------------------------------------------

    def test_protocol_mismatch_raises_type_error(self):
        """A class that doesn't implement check_deconfliction raises TypeError."""
        with self.assertRaises(TypeError):
            load_plugin(
                "flight_blender.domain_types.flight_declarations.DeconflictionRequest",
                expected_protocol=DeconflictionEngineProtocol,
            )

    def test_valid_protocol_passes(self):
        cls = load_plugin(
            "flight_blender.services.deconfliction_engine.DefaultDeconflictionEngine",
            expected_protocol=DeconflictionEngineProtocol,
        )
        self.assertIs(cls, DefaultDeconflictionEngine)

    def test_example_engine_passes_protocol_check(self):
        cls = load_plugin(
            "flight_blender.plugins.examples.altitude_aware_deconfliction_engine.AltitudeAwareDeconflictionEngine",
            expected_protocol=DeconflictionEngineProtocol,
        )
        self.assertIs(cls, AltitudeAwareDeconflictionEngine)

    def test_no_protocol_skips_validation(self):
        """Without expected_protocol any class is accepted."""
        cls = load_plugin("flight_blender.domain_types.flight_declarations.DeconflictionRequest")
        self.assertIs(cls, DeconflictionRequest)

    # -- caching -----------------------------------------------------------

    def test_same_path_returns_same_object(self):
        path = "flight_blender.services.deconfliction_engine.DefaultDeconflictionEngine"
        cls1 = load_plugin(path)
        cls2 = load_plugin(path)
        self.assertIs(cls1, cls2)

    def test_cache_info_reflects_hits(self):
        path = "flight_blender.services.deconfliction_engine.DefaultDeconflictionEngine"
        load_plugin(path)
        load_plugin(path)
        info = load_plugin.cache_info()
        self.assertGreaterEqual(info.hits, 1)

    def test_cache_clear_resets(self):
        path = "flight_blender.services.deconfliction_engine.DefaultDeconflictionEngine"
        load_plugin(path)
        load_plugin.cache_clear()
        info = load_plugin.cache_info()
        self.assertEqual(info.hits, 0)
        self.assertEqual(info.misses, 0)


# ---------------------------------------------------------------------------
# DeconflictionEngine protocol
# ---------------------------------------------------------------------------


class DeconflictionProtocolTests(TestCase):
    """Tests for the DeconflictionEngine protocol conformance."""

    def test_default_engine_is_instance_of_protocol(self):
        engine = DefaultDeconflictionEngine()
        self.assertIsInstance(engine, DeconflictionEngineProtocol)

    def test_example_engine_is_instance_of_protocol(self):
        engine = AltitudeAwareDeconflictionEngine()
        self.assertIsInstance(engine, DeconflictionEngineProtocol)

    def test_plain_object_is_not_deconfliction_engine(self):
        """An object without check_deconfliction is not a DeconflictionEngine."""
        self.assertNotIsInstance(object(), DeconflictionEngineProtocol)

    def test_protocol_is_runtime_checkable(self):
        """DeconflictionEngine is decorated with @runtime_checkable."""
        # isinstance() only works on @runtime_checkable protocols
        self.assertIsInstance(DefaultDeconflictionEngine(), DeconflictionEngineProtocol)


# ---------------------------------------------------------------------------
# DeconflictionEngine — functional tests (example engine, no DB needed)
# ---------------------------------------------------------------------------


class ExampleDeconflictionEngineTests(TestCase):
    """Functional tests for AltitudeAwareDeconflictionEngine."""

    def _make_request(self, **overrides) -> DeconflictionRequest:
        defaults = dict(
            start_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
            view_box=[0.0, 0.0, 1.0, 1.0],
            ussp_network_enabled=0,
        )
        defaults.update(overrides)
        return DeconflictionRequest(**defaults)

    def test_example_engine_returns_deconfliction_result(self):
        engine = AltitudeAwareDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request())
        self.assertIsInstance(result, DeconflictionResult)

    def test_example_engine_always_approves(self):
        engine = AltitudeAwareDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request())
        self.assertTrue(result.is_approved)
        self.assertEqual(result.all_relevant_fences, [])
        self.assertEqual(result.all_relevant_declarations, [])

    def test_example_engine_state_with_ussp_disabled(self):
        engine = AltitudeAwareDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request(ussp_network_enabled=0))
        self.assertEqual(result.declaration_state, 1)

    def test_example_engine_state_with_ussp_enabled(self):
        engine = AltitudeAwareDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request(ussp_network_enabled=1))
        self.assertEqual(result.declaration_state, 0)

    def test_example_engine_receives_geojson(self):
        """The request's flight_declaration_geo_json is available to the engine."""
        geo = {"type": "FeatureCollection", "features": []}
        engine = AltitudeAwareDeconflictionEngine()
        req = self._make_request(flight_declaration_geo_json=geo)
        # The example engine doesn't use it, but it shouldn't raise
        result = engine.check_deconfliction(req)
        self.assertTrue(result.is_approved)

    def test_example_engine_with_priority_and_type(self):
        """Extra fields on the request are accessible."""
        engine = AltitudeAwareDeconflictionEngine()
        req = self._make_request(type_of_operation=2, priority=5)
        result = engine.check_deconfliction(req)
        self.assertTrue(result.is_approved)


# ---------------------------------------------------------------------------
# DefaultDeconflictionEngine — with mocked DB queries
# ---------------------------------------------------------------------------


class DeconflictionDataClassTests(TestCase):
    """Tests for DeconflictionRequest and DeconflictionResult dataclasses."""

    def test_request_required_fields(self):
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        req = DeconflictionRequest(
            start_datetime=start,
            end_datetime=end,
            view_box=[0.0, 0.0, 1.0, 1.0],
            ussp_network_enabled=0,
        )
        self.assertEqual(req.start_datetime, start)
        self.assertEqual(req.end_datetime, end)
        self.assertEqual(req.view_box, [0.0, 0.0, 1.0, 1.0])
        self.assertEqual(req.ussp_network_enabled, 0)

    def test_request_defaults(self):
        req = DeconflictionRequest(
            start_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
            view_box=[0.0, 0.0, 1.0, 1.0],
            ussp_network_enabled=0,
        )
        self.assertIsNone(req.declaration_id)
        self.assertIsNone(req.flight_declaration_geo_json)
        self.assertEqual(req.type_of_operation, 0)
        self.assertEqual(req.priority, 0)

    def test_request_all_fields(self):
        geo = {"type": "FeatureCollection", "features": []}
        req = DeconflictionRequest(
            start_datetime=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
            view_box=[1.0, 2.0, 3.0, 4.0],
            ussp_network_enabled=1,
            declaration_id="decl-123",
            flight_declaration_geo_json=geo,
            type_of_operation=2,
            priority=5,
        )
        self.assertEqual(req.declaration_id, "decl-123")
        self.assertEqual(req.flight_declaration_geo_json, geo)
        self.assertEqual(req.type_of_operation, 2)
        self.assertEqual(req.priority, 5)

    def test_result_fields(self):
        result = DeconflictionResult(
            all_relevant_fences=[{"id": "f1"}],
            all_relevant_declarations=[{"id": "d1"}, {"id": "d2"}],
            is_approved=False,
            declaration_state=8,
        )
        self.assertEqual(len(result.all_relevant_fences), 1)
        self.assertEqual(len(result.all_relevant_declarations), 2)
        self.assertFalse(result.is_approved)
        self.assertEqual(result.declaration_state, 8)

    def test_backward_compat_alias(self):
        self.assertIs(IntersectionCheckResult, DeconflictionResult)

    def test_backward_compat_construction(self):
        result = IntersectionCheckResult(
            all_relevant_fences=[],
            all_relevant_declarations=[],
            is_approved=True,
            declaration_state=1,
        )
        self.assertIsInstance(result, DeconflictionResult)
        self.assertTrue(result.is_approved)


# ---------------------------------------------------------------------------
# TrafficDataFuser — basic interface check (no Protocol layer anymore)
# ---------------------------------------------------------------------------


class TrafficDataFuserProtocolTests(TestCase):
    """Tests for the TrafficDataFuser protocol."""

    def test_protocol_is_runtime_checkable(self):
        """isinstance() works on TrafficDataFuserProtocol."""

        class GoodFuser:
            def generate_track_messages(self):
                return []

        self.assertIsInstance(GoodFuser(), TrafficDataFuserProtocol)

    def test_class_without_method_not_instance(self):
        class BadFuser:
            pass

        self.assertNotIsInstance(BadFuser(), TrafficDataFuserProtocol)

    def test_default_fuser_has_correct_method(self):
        """The default TrafficDataFuser in services has generate_track_messages."""

        self.assertTrue(hasattr(TrafficDataFuser, "generate_track_messages"))
        self.assertTrue(callable(getattr(TrafficDataFuser, "generate_track_messages")))

    def test_load_plugin_with_fuser_class(self):
        """load_plugin returns the fuser class."""
        load_plugin.cache_clear()
        cls = load_plugin("flight_blender.services.surveillance_svc.TrafficDataFuser", expected_protocol=TrafficDataFuserProtocol)
        self.assertIs(cls, TrafficDataFuser)
        load_plugin.cache_clear()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class PluginSettingsTests(TestCase):
    """Tests for plugin-related settings."""

    def test_default_deconfliction_engine_setting(self):
        from flight_blender.config import settings

        self.assertEqual(
            settings.FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE,
            "flight_blender.services.deconfliction_engine.DefaultDeconflictionEngine",
        )

    def test_default_traffic_data_fuser_setting(self):
        from flight_blender.config import settings

        self.assertIsInstance(settings.FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER, str)

    def test_default_volume_generator_setting(self):
        from flight_blender.config import settings

        # Default is empty string (inherited from CUSTOM_VOLUME_4D_GENERATION_CLASS default)
        self.assertIsInstance(settings.FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR, str)
