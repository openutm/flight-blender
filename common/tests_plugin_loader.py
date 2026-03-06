"""Tests for the universal plugin loader and plugin-based extension points.

Covers:
- ``load_plugin`` importing, caching, error handling, and protocol validation
- ``DeconflictionEngine`` protocol (default engine, example engine, functional)
- ``TrafficDataFuser`` protocol conformance
- ``DeconflictionResult`` backward-compat alias (``IntersectionCheckResult``)
- ``DeconflictionRequest`` defaults and field assignment
- Plugin settings (new prefix, backward-compat fallback)
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from common.plugin_loader import load_plugin
from flight_declaration_operations.data_definitions import (
    DeconflictionRequest,
    DeconflictionResult,
    IntersectionCheckResult,
)
from flight_declaration_operations.deconfliction_engine import DefaultDeconflictionEngine
from flight_declaration_operations.deconfliction_protocol import DeconflictionEngine
from flight_declaration_operations.example_deconfliction_engine import (
    AltitudeAwareDeconflictionEngine,
)
from surveillance_monitoring_operations.traffic_data_fuser_protocol import (
    TrafficDataFuser as TrafficDataFuserProtocol,
)


# ---------------------------------------------------------------------------
# load_plugin — core mechanics
# ---------------------------------------------------------------------------


class LoadPluginTests(SimpleTestCase):
    """Tests for common.plugin_loader.load_plugin."""

    def setUp(self):
        load_plugin.cache_clear()

    def tearDown(self):
        load_plugin.cache_clear()

    # -- valid path --------------------------------------------------------

    def test_valid_path_returns_correct_class(self):
        cls = load_plugin(
            "flight_declaration_operations.deconfliction_engine.DefaultDeconflictionEngine"
        )
        self.assertIs(cls, DefaultDeconflictionEngine)

    def test_valid_path_different_module(self):
        """load_plugin works across modules."""
        cls = load_plugin(
            "flight_declaration_operations.example_deconfliction_engine."
            "AltitudeAwareDeconflictionEngine"
        )
        self.assertIs(cls, AltitudeAwareDeconflictionEngine)

    # -- invalid paths -----------------------------------------------------

    def test_invalid_module_raises_import_error(self):
        with self.assertRaises((ImportError, ModuleNotFoundError)):
            load_plugin("totally.fake.module.ClassName")

    def test_invalid_class_raises_attribute_error(self):
        with self.assertRaises(AttributeError):
            load_plugin(
                "flight_declaration_operations.deconfliction_engine.NonExistentClass"
            )

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
                "flight_declaration_operations.data_definitions.DeconflictionRequest",
                expected_protocol=DeconflictionEngine,
            )

    def test_valid_protocol_passes(self):
        cls = load_plugin(
            "flight_declaration_operations.deconfliction_engine.DefaultDeconflictionEngine",
            expected_protocol=DeconflictionEngine,
        )
        self.assertIs(cls, DefaultDeconflictionEngine)

    def test_example_engine_passes_protocol_check(self):
        cls = load_plugin(
            "flight_declaration_operations.example_deconfliction_engine."
            "AltitudeAwareDeconflictionEngine",
            expected_protocol=DeconflictionEngine,
        )
        self.assertIs(cls, AltitudeAwareDeconflictionEngine)

    def test_no_protocol_skips_validation(self):
        """Without expected_protocol any class is accepted."""
        cls = load_plugin(
            "flight_declaration_operations.data_definitions.DeconflictionRequest"
        )
        self.assertIs(cls, DeconflictionRequest)

    # -- caching -----------------------------------------------------------

    def test_same_path_returns_same_object(self):
        path = "flight_declaration_operations.deconfliction_engine.DefaultDeconflictionEngine"
        cls1 = load_plugin(path)
        cls2 = load_plugin(path)
        self.assertIs(cls1, cls2)

    def test_cache_info_reflects_hits(self):
        path = "flight_declaration_operations.deconfliction_engine.DefaultDeconflictionEngine"
        load_plugin(path)
        load_plugin(path)
        info = load_plugin.cache_info()
        self.assertGreaterEqual(info.hits, 1)

    def test_cache_clear_resets(self):
        path = "flight_declaration_operations.deconfliction_engine.DefaultDeconflictionEngine"
        load_plugin(path)
        load_plugin.cache_clear()
        info = load_plugin.cache_info()
        self.assertEqual(info.hits, 0)
        self.assertEqual(info.misses, 0)


# ---------------------------------------------------------------------------
# DeconflictionEngine protocol
# ---------------------------------------------------------------------------


class DeconflictionProtocolTests(SimpleTestCase):
    """Tests for the DeconflictionEngine protocol conformance."""

    def test_default_engine_is_instance_of_protocol(self):
        engine = DefaultDeconflictionEngine()
        self.assertIsInstance(engine, DeconflictionEngine)

    def test_example_engine_is_instance_of_protocol(self):
        engine = AltitudeAwareDeconflictionEngine()
        self.assertIsInstance(engine, DeconflictionEngine)

    def test_plain_object_is_not_deconfliction_engine(self):
        """An object without check_deconfliction is not a DeconflictionEngine."""
        self.assertNotIsInstance(object(), DeconflictionEngine)

    def test_protocol_is_runtime_checkable(self):
        """DeconflictionEngine is decorated with @runtime_checkable."""
        # isinstance() only works on @runtime_checkable protocols
        self.assertIsInstance(DefaultDeconflictionEngine(), DeconflictionEngine)


# ---------------------------------------------------------------------------
# DeconflictionEngine — functional tests (example engine, no DB needed)
# ---------------------------------------------------------------------------


class ExampleDeconflictionEngineTests(SimpleTestCase):
    """Functional tests for AltitudeAwareDeconflictionEngine."""

    def _make_request(self, **overrides) -> DeconflictionRequest:
        defaults = dict(
            start_datetime="2026-01-01T00:00:00Z",
            end_datetime="2026-01-01T01:00:00Z",
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


class DefaultDeconflictionEngineTests(SimpleTestCase):
    """Tests for the default RTree engine with mocked DB and index ops."""

    def _make_request(self, **overrides) -> DeconflictionRequest:
        defaults = dict(
            start_datetime="2026-01-01T00:00:00Z",
            end_datetime="2026-01-01T01:00:00Z",
            view_box=[0.0, 0.0, 1.0, 1.0],
            ussp_network_enabled=0,
        )
        defaults.update(overrides)
        return DeconflictionRequest(**defaults)

    @patch("flight_declaration_operations.deconfliction_engine.FlightDeclaration.objects")
    @patch("flight_declaration_operations.deconfliction_engine.GeoFence.objects")
    def test_no_fences_no_declarations_approves(self, mock_gf_objects, mock_fd_objects):
        """No geofences and no declarations → approved."""
        mock_gf_objects.filter.return_value = []
        mock_fd_objects.filter.return_value = []

        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request())

        self.assertTrue(result.is_approved)
        self.assertEqual(result.all_relevant_fences, [])
        self.assertEqual(result.all_relevant_declarations, [])
        self.assertEqual(result.declaration_state, 1)  # USSP disabled

    @patch("flight_declaration_operations.deconfliction_engine.FlightDeclaration.objects")
    @patch("flight_declaration_operations.deconfliction_engine.GeoFence.objects")
    def test_no_data_ussp_enabled_state_zero(self, mock_gf_objects, mock_fd_objects):
        """With USSP enabled and no conflicts → state=0."""
        mock_gf_objects.filter.return_value = []
        mock_fd_objects.filter.return_value = []

        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request(ussp_network_enabled=1))

        self.assertTrue(result.is_approved)
        self.assertEqual(result.declaration_state, 0)

    @patch("flight_declaration_operations.deconfliction_engine.FlightDeclaration.objects")
    @patch("flight_declaration_operations.deconfliction_engine.GeoFence.objects")
    @patch("flight_declaration_operations.deconfliction_engine.rtree_geo_fence_helper.GeoFenceRTreeIndexFactory")
    def test_geofence_intersection_rejects(self, mock_index_cls, mock_gf_objects, mock_fd_objects):
        """A geofence bbox conflict → rejected (state=8)."""
        fence = MagicMock()
        mock_gf_objects.filter.return_value = [fence]
        mock_fd_objects.filter.return_value = []

        mock_index = MagicMock()
        mock_index.check_box_intersection.return_value = [{"id": "fence-1"}]
        mock_index_cls.return_value = mock_index

        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request())

        self.assertFalse(result.is_approved)
        self.assertEqual(result.declaration_state, 8)
        self.assertEqual(result.all_relevant_fences, [{"id": "fence-1"}])
        mock_index.clear_rtree_index.assert_called_once()

    @patch("flight_declaration_operations.deconfliction_engine.FlightDeclarationRTreeIndexFactory")
    @patch("flight_declaration_operations.deconfliction_engine.FlightDeclaration.objects")
    @patch("flight_declaration_operations.deconfliction_engine.GeoFence.objects")
    def test_flight_declaration_intersection_rejects(
        self, mock_gf_objects, mock_fd_objects, mock_fd_index_cls,
    ):
        """Active flight declaration bbox conflict → rejected (state=8)."""
        mock_gf_objects.filter.return_value = []

        decl = MagicMock()
        mock_fd_objects.filter.return_value = [decl]

        mock_index = MagicMock()
        mock_index.check_flight_declaration_box_intersection.return_value = [{"id": "decl-99"}]
        mock_fd_index_cls.return_value = mock_index

        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request())

        self.assertFalse(result.is_approved)
        self.assertEqual(result.declaration_state, 8)
        self.assertEqual(result.all_relevant_declarations, [{"id": "decl-99"}])
        mock_index.clear_rtree_index.assert_called_once()

    @patch("flight_declaration_operations.deconfliction_engine.FlightDeclarationRTreeIndexFactory")
    @patch("flight_declaration_operations.deconfliction_engine.FlightDeclaration.objects")
    @patch("flight_declaration_operations.deconfliction_engine.GeoFence.objects")
    @patch("flight_declaration_operations.deconfliction_engine.rtree_geo_fence_helper.GeoFenceRTreeIndexFactory")
    def test_both_fence_and_declaration_conflict(
        self, mock_gf_index_cls, mock_gf_objects, mock_fd_objects, mock_fd_index_cls,
    ):
        """When both geofence AND declaration conflicts exist, both are reported."""
        fence = MagicMock()
        mock_gf_objects.filter.return_value = [fence]
        gf_index = MagicMock()
        gf_index.check_box_intersection.return_value = [{"id": "fence-1"}]
        mock_gf_index_cls.return_value = gf_index

        decl = MagicMock()
        mock_fd_objects.filter.return_value = [decl]
        fd_index = MagicMock()
        fd_index.check_flight_declaration_box_intersection.return_value = [{"id": "decl-1"}]
        mock_fd_index_cls.return_value = fd_index

        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request())

        self.assertFalse(result.is_approved)
        self.assertEqual(result.declaration_state, 8)
        self.assertIn({"id": "fence-1"}, result.all_relevant_fences)
        self.assertIn({"id": "decl-1"}, result.all_relevant_declarations)

    @patch("flight_declaration_operations.deconfliction_engine.FlightDeclaration.objects")
    @patch("flight_declaration_operations.deconfliction_engine.GeoFence.objects")
    @patch("flight_declaration_operations.deconfliction_engine.rtree_geo_fence_helper.GeoFenceRTreeIndexFactory")
    def test_geofence_no_intersection_still_approved(
        self, mock_index_cls, mock_gf_objects, mock_fd_objects,
    ):
        """Geofences exist but no bbox overlap → still approved."""
        fence = MagicMock()
        mock_gf_objects.filter.return_value = [fence]
        mock_fd_objects.filter.return_value = []

        mock_index = MagicMock()
        mock_index.check_box_intersection.return_value = []
        mock_index_cls.return_value = mock_index

        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(self._make_request())

        self.assertTrue(result.is_approved)
        self.assertEqual(result.all_relevant_fences, [])
        mock_index.clear_rtree_index.assert_called_once()

    def test_returns_deconfliction_result_type(self):
        """Result type is DeconflictionResult (and hence IntersectionCheckResult)."""
        with patch("flight_declaration_operations.deconfliction_engine.GeoFence.objects") as mock_gf, \
             patch("flight_declaration_operations.deconfliction_engine.FlightDeclaration.objects") as mock_fd:
            mock_gf.filter.return_value = []
            mock_fd.filter.return_value = []

            engine = DefaultDeconflictionEngine()
            result = engine.check_deconfliction(self._make_request())

            self.assertIsInstance(result, DeconflictionResult)
            self.assertIsInstance(result, IntersectionCheckResult)


# ---------------------------------------------------------------------------
# DeconflictionRequest / DeconflictionResult data classes
# ---------------------------------------------------------------------------


class DeconflictionDataClassTests(SimpleTestCase):
    """Tests for DeconflictionRequest and DeconflictionResult dataclasses."""

    def test_request_required_fields(self):
        req = DeconflictionRequest(
            start_datetime="2026-01-01T00:00:00Z",
            end_datetime="2026-01-01T01:00:00Z",
            view_box=[0.0, 0.0, 1.0, 1.0],
            ussp_network_enabled=0,
        )
        self.assertEqual(req.start_datetime, "2026-01-01T00:00:00Z")
        self.assertEqual(req.end_datetime, "2026-01-01T01:00:00Z")
        self.assertEqual(req.view_box, [0.0, 0.0, 1.0, 1.0])
        self.assertEqual(req.ussp_network_enabled, 0)

    def test_request_defaults(self):
        req = DeconflictionRequest(
            start_datetime="2026-01-01T00:00:00Z",
            end_datetime="2026-01-01T01:00:00Z",
            view_box=[0.0, 0.0, 1.0, 1.0],
            ussp_network_enabled=0,
        )
        self.assertIsNone(req.flight_declaration_geo_json)
        self.assertEqual(req.type_of_operation, 0)
        self.assertEqual(req.priority, 0)

    def test_request_all_fields(self):
        geo = {"type": "FeatureCollection", "features": []}
        req = DeconflictionRequest(
            start_datetime="2026-01-01T00:00:00Z",
            end_datetime="2026-01-01T01:00:00Z",
            view_box=[1.0, 2.0, 3.0, 4.0],
            ussp_network_enabled=1,
            flight_declaration_geo_json=geo,
            type_of_operation=2,
            priority=5,
        )
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
# _run_deconfliction helper (views.py)
# ---------------------------------------------------------------------------


class RunDeconflictionTests(SimpleTestCase):
    """Tests for the _run_deconfliction helper function in views.py."""

    def _make_fake_fd(self, fd_id="fd-001", bounds="0.0,0.0,1.0,1.0", geojson=None):
        fd = MagicMock()
        fd.id = fd_id
        fd.bounds = bounds
        fd.flight_declaration_raw_geojson = geojson
        fd.start_datetime = "2026-01-01T00:00:00Z"
        fd.end_datetime = "2026-01-01T01:00:00Z"
        fd.type_of_operation = 1
        return fd

    @patch("flight_declaration_operations.views._DeconflictionEngineClass")
    def test_empty_list_returns_empty_dict(self, mock_engine_cls):
        from flight_declaration_operations.views import _run_deconfliction

        result = _run_deconfliction([], 0)
        self.assertEqual(result, {})
        mock_engine_cls.assert_not_called()

    @patch("flight_declaration_operations.views._DeconflictionEngineClass")
    def test_single_declaration_calls_engine(self, mock_engine_cls):
        from flight_declaration_operations.views import _run_deconfliction

        mock_engine = MagicMock()
        expected_result = DeconflictionResult(
            all_relevant_fences=[],
            all_relevant_declarations=[],
            is_approved=True,
            declaration_state=1,
        )
        mock_engine.check_deconfliction.return_value = expected_result
        mock_engine_cls.return_value = mock_engine

        fd = self._make_fake_fd()
        results = _run_deconfliction([fd], 0)

        self.assertIn("fd-001", results)
        self.assertIs(results["fd-001"], expected_result)
        mock_engine.check_deconfliction.assert_called_once()

        # Verify the request passed to the engine
        call_args = mock_engine.check_deconfliction.call_args[0][0]
        self.assertIsInstance(call_args, DeconflictionRequest)
        self.assertEqual(call_args.ussp_network_enabled, 0)

    @patch("flight_declaration_operations.views._DeconflictionEngineClass")
    def test_multiple_declarations_evaluated_individually(self, mock_engine_cls):
        from flight_declaration_operations.views import _run_deconfliction

        approved_result = DeconflictionResult(
            all_relevant_fences=[], all_relevant_declarations=[],
            is_approved=True, declaration_state=1,
        )
        rejected_result = DeconflictionResult(
            all_relevant_fences=[{"id": "f1"}], all_relevant_declarations=[],
            is_approved=False, declaration_state=8,
        )

        mock_engine = MagicMock()
        mock_engine.check_deconfliction.side_effect = [approved_result, rejected_result]
        mock_engine_cls.return_value = mock_engine

        fd1 = self._make_fake_fd(fd_id="fd-A")
        fd2 = self._make_fake_fd(fd_id="fd-B")
        results = _run_deconfliction([fd1, fd2], 0)

        self.assertEqual(len(results), 2)
        self.assertTrue(results["fd-A"].is_approved)
        self.assertFalse(results["fd-B"].is_approved)

    @patch("flight_declaration_operations.views._DeconflictionEngineClass")
    def test_geojson_parsed_from_raw(self, mock_engine_cls):
        from flight_declaration_operations.views import _run_deconfliction

        result = DeconflictionResult(
            all_relevant_fences=[], all_relevant_declarations=[],
            is_approved=True, declaration_state=1,
        )
        mock_engine = MagicMock()
        mock_engine.check_deconfliction.return_value = result
        mock_engine_cls.return_value = mock_engine

        raw_geo = '{"type": "FeatureCollection", "features": []}'
        fd = self._make_fake_fd(geojson=raw_geo)
        _run_deconfliction([fd], 0)

        call_args = mock_engine.check_deconfliction.call_args[0][0]
        self.assertEqual(call_args.flight_declaration_geo_json, {"type": "FeatureCollection", "features": []})

    @patch("flight_declaration_operations.views._DeconflictionEngineClass")
    def test_null_geojson_is_none(self, mock_engine_cls):
        from flight_declaration_operations.views import _run_deconfliction

        result = DeconflictionResult(
            all_relevant_fences=[], all_relevant_declarations=[],
            is_approved=True, declaration_state=1,
        )
        mock_engine = MagicMock()
        mock_engine.check_deconfliction.return_value = result
        mock_engine_cls.return_value = mock_engine

        fd = self._make_fake_fd(geojson=None)
        _run_deconfliction([fd], 0)

        call_args = mock_engine.check_deconfliction.call_args[0][0]
        self.assertIsNone(call_args.flight_declaration_geo_json)


# ---------------------------------------------------------------------------
# TrafficDataFuser protocol
# ---------------------------------------------------------------------------


class TrafficDataFuserProtocolTests(SimpleTestCase):
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
        """The default TrafficDataFuser in utils.py has generate_track_messages."""
        from surveillance_monitoring_operations.utils import TrafficDataFuser

        self.assertTrue(hasattr(TrafficDataFuser, "generate_track_messages"))
        self.assertTrue(callable(getattr(TrafficDataFuser, "generate_track_messages")))

    def test_load_plugin_with_fuser_protocol(self):
        """load_plugin accepts the default fuser class against the protocol."""
        load_plugin.cache_clear()
        cls = load_plugin(
            "surveillance_monitoring_operations.utils.TrafficDataFuser",
            expected_protocol=TrafficDataFuserProtocol,
        )
        from surveillance_monitoring_operations.utils import TrafficDataFuser

        self.assertIs(cls, TrafficDataFuser)
        load_plugin.cache_clear()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class PluginSettingsTests(SimpleTestCase):
    """Tests for plugin-related settings."""

    def test_default_deconfliction_engine_setting(self):
        from flight_blender.settings import FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE

        self.assertEqual(
            FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE,
            "flight_declaration_operations.deconfliction_engine.DefaultDeconflictionEngine",
        )

    def test_default_traffic_data_fuser_setting(self):
        from flight_blender.settings import FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER

        self.assertEqual(
            FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER,
            "surveillance_monitoring_operations.utils.TrafficDataFuser",
        )

    def test_default_volume_generator_setting(self):
        from flight_blender.settings import FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR

        # Default is empty string (inherited from CUSTOM_VOLUME_4D_GENERATION_CLASS default)
        self.assertIsInstance(FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR, str)

    def test_backward_compat_settings_exist(self):
        """Old setting names are preserved for backward compatibility."""
        from flight_blender import settings

        self.assertTrue(hasattr(settings, "ASTM_F3623_SDSP_CUSTOM_DATA_FUSER_CLASS"))
        self.assertTrue(hasattr(settings, "CUSTOM_VOLUME_4D_GENERATION_CLASS"))

    def test_backward_compat_fuser_matches_new(self):
        """Old and new fuser setting have the same default value."""
        from flight_blender.settings import (
            ASTM_F3623_SDSP_CUSTOM_DATA_FUSER_CLASS,
            FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER,
        )

        self.assertEqual(
            FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER,
            ASTM_F3623_SDSP_CUSTOM_DATA_FUSER_CLASS,
        )
