"""Protocol definition for pluggable de-confliction engines.

Any class that implements ``check_deconfliction`` with the correct signature
is a valid ``DeconflictionEngine`` — no inheritance required (structural
subtyping via ``typing.Protocol``).
"""

from typing import Protocol, runtime_checkable

from flight_declaration_operations.data_definitions import (
    DeconflictionRequest,
    DeconflictionResult,
)


@runtime_checkable
class DeconflictionEngine(Protocol):
    """Structural interface for flight de-confliction engines.

    Any class that implements ``check_deconfliction`` with the correct
    signature is a valid DeconflictionEngine — no inheritance required.
    """

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        """Evaluate a flight declaration against existing operations and geofences.

        Args:
            request: All data needed to perform de-confliction.

        Returns:
            A ``DeconflictionResult`` containing the approval state and
            any conflicting entities.
        """
        ...
