"""Example: altitude-aware de-confliction engine.

This module is a documented stub showing third parties how to build a custom
de-confliction engine for Flight Blender.

To use, set the environment variable::

    FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE=flight_declaration_operations.example_deconfliction_engine.AltitudeAwareDeconflictionEngine

The class must satisfy the
:class:`~flight_declaration_operations.deconfliction_protocol.DeconflictionEngine`
protocol by implementing a ``check_deconfliction`` method that accepts a
:class:`~flight_declaration_operations.data_definitions.DeconflictionRequest`
and returns a
:class:`~flight_declaration_operations.data_definitions.DeconflictionResult`.
"""

from flight_declaration_operations.data_definitions import (
    DeconflictionRequest,
    DeconflictionResult,
)


class AltitudeAwareDeconflictionEngine:
    """Demonstrates a custom engine that could add altitude separation logic.

    This is intentionally minimal — replace the body of
    ``check_deconfliction`` with your own algorithms (3-D intersection,
    separation minima, ML-based prediction, batch optimisation, etc.).
    """

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        """Evaluate a flight declaration with custom logic.

        Args:
            request: All data needed to perform de-confliction. The
                ``flight_declaration_geo_json`` field carries the full
                GeoJSON FeatureCollection so advanced engines can inspect
                per-feature altitudes, geometry types, etc.

        Returns:
            A ``DeconflictionResult`` with the approval decision.
        """
        # ── Placeholder: always approve ──────────────────────────────────
        # A real implementation would inspect request.flight_declaration_geo_json,
        # request.type_of_operation, request.priority, etc.
        return DeconflictionResult(
            all_relevant_fences=[],
            all_relevant_declarations=[],
            is_approved=True,
            declaration_state=0 if request.ussp_network_enabled else 1,
        )
