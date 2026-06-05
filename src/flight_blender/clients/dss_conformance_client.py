"""DSS conformance client — re-exports for backward compatibility.

Orchestration logic has been moved to ``services/conformance_svc.py``.
"""

from flight_blender.clients.dss_scd_client import OperationalIntentReferenceHelper, SCDOperations

__all__ = ["OperationalIntentReferenceHelper", "SCDOperations"]
