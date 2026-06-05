# Writing Plugins for Flight Blender

Flight Blender uses a plugin system that lets you replace core components with your own implementations — no forking required. This guide walks through the architecture and shows you how to build a plugin from scratch.

## How It Works

The plugin system is built on three concepts:

1. **Protocols** — Python `typing.Protocol` classes that define the method signatures your plugin must implement (structural subtyping, no inheritance required).
2. **Dotted-path settings** — Each extension point has a pydantic-settings config value (backed by an environment variable) that holds the fully qualified class path of the plugin to load.
3. **`load_plugin()`** — A loader function that imports the class, validates it against the protocol, caches it, and returns it to the caller.

```
Environment variable
    └─▶ pydantic-settings config (dotted class path)
            └─▶ load_plugin()  ──▶  import + validate + cache
                                         └─▶  caller instantiates your class
```

## Available Extension Points

| Extension Point | Environment Variable | Protocol | Default Implementation |
|---|---|---|---|
| De-confliction engine | `FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE` | `DeconflictionEngineProtocol` | `flight_blender.services.deconfliction_engine.DefaultDeconflictionEngine` |
| Traffic data fuser | `FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER` | `TrafficDataFuserProtocol` | `flight_blender.services.surveillance_svc.TrafficDataFuser` |
| Volume 4D generator | `FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR` | `Volume4DGeneratorProtocol` | _(empty — disabled by default)_ |

## Quick Start

The `src/flight_blender/plugins/examples/` directory ships with a working example for every extension point. To try one out:

### 1. Pick a plugin

The project includes these ready-to-use examples:

| Example | File | What it does |
|---|---|---|
| De-confliction engine | `src/flight_blender/plugins/examples/hello_world_engine.py` | Rejects declarations that overlap existing accepted flights by time window |
| Traffic data fuser | `src/flight_blender/plugins/examples/hello_world_fuser.py` | De-duplicates observations per aircraft, drops stale data, emits latest position |
| Volume 4D generator | `src/flight_blender/plugins/examples/hello_world_volume_generator.py` | Splits the time window across features proportionally to segment length |

### 2. Point the setting to the class

```bash
export FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE=flight_blender.plugins.examples.hello_world_engine.HelloWorldEngine
```

Or add it to your `.env` file if you use one.

### 3. Start Flight Blender

Launch as normal. The framework loads your class automatically and calls it in place of the default.

### Writing your own

Create a file anywhere on the Python path. Your class only needs to implement the method(s) defined by the protocol — no inheritance required. Here's the minimal skeleton for a de-confliction engine:

```python
# my_plugins/my_engine.py

from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.domain_types.flight_declarations import (
    DeconflictionRequest,
    DeconflictionResult,
)


class MyEngine:
    async def check_deconfliction(
        self, request: DeconflictionRequest, db: AsyncSession
    ) -> DeconflictionResult:
        # Your logic here
        return DeconflictionResult(
            all_relevant_fences=[],
            all_relevant_declarations=[],
            is_approved=True,
            declaration_state=0,
        )
```

Make sure the directory has an `__init__.py` so Python treats it as a package.

## Writing a Real Plugin

### Understand the Protocol

Each extension point has a protocol class that defines the contract. For example, the de-confliction engine protocol is:

```python
# flight_blender/domain_types/plugin_protocols.py

from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class DeconflictionEngineProtocol(Protocol):
    async def check_deconfliction(
        self, request: DeconflictionRequest, db: AsyncSession
    ) -> DeconflictionResult: ...
```

Your class must implement every method in the protocol with matching signatures. You do **not** need to inherit from the protocol or import it — Python's structural subtyping handles the rest. The loader will verify conformance at startup and raise a `TypeError` if something is missing.

### Input and Output Types

#### `DeconflictionRequest`

| Field | Type | Description |
|---|---|---|
| `start_datetime` | `datetime` | Start of the operation window |
| `end_datetime` | `datetime` | End of the operation window |
| `view_box` | `list[float]` | Bounding box `[minx, miny, maxx, maxy]` |
| `ussp_network_enabled` | `int` | Whether the USSP network is active |
| `declaration_id` | `str \| None` | ID of the declaration being evaluated (to exclude self-conflicts) |
| `flight_declaration_geo_json` | `dict \| None` | Full GeoJSON FeatureCollection of the declaration |
| `type_of_operation` | `int` | Operation type code |
| `priority` | `int` | Priority level |

#### `DeconflictionResult`

| Field | Type | Description |
|---|---|---|
| `all_relevant_fences` | `list` | Conflicting geofence metadata |
| `all_relevant_declarations` | `list` | Conflicting flight declaration metadata |
| `is_approved` | `bool` | Whether the declaration is approved |
| `declaration_state` | `int` | Operation state value (e.g., 0 = accepted, 1 = accepted with conditions, 8 = rejected) |

### Traffic Data Fuser Protocol

```python
# flight_blender/domain_types/plugin_protocols.py


@runtime_checkable
class TrafficDataFuserProtocol(Protocol):
    def generate_track_messages(self) -> list[TrackMessage]: ...
```

Traffic data fusers are instantiated with `(session_id: str, raw_observations: list)`. The optional `BaseTrafficDataFuser` base class in `common/base_traffic_data_fuser.py` provides helper methods for speed/bearing calculation and track message generation — you can extend it for convenience, but it's not required.

### Volume 4D Generator Protocol

```python
# flight_blender/domain_types/plugin_protocols.py


@runtime_checkable
class Volume4DGeneratorProtocol(Protocol):
    def build_v4d_from_geojson(
        self,
        geo_json_fc: dict,
        start_datetime: str,
        end_datetime: str,
    ) -> list[Volume4D]: ...
```

Volume 4D generators are instantiated with `(default_uav_speed_m_per_s: float, default_uav_climb_rate_m_per_s: float, default_uav_descent_rate_m_per_s: float)`. The constructor parameters are UAV performance characteristics used for time-proportioning calculations.

## Example Plugins

The `src/flight_blender/plugins/examples/` directory ships with a working example for **every** extension point. Each can be activated with a single environment variable.

### De-confliction Engine — `hello_world_engine.py`

Rejects flight declarations whose time window overlaps an existing accepted declaration. Uses a simple database query (no spatial indexing) — easy to understand and extend with your own conflict rules.

```python
class HelloWorldEngine:
    async def check_deconfliction(
        self, request: DeconflictionRequest, db: AsyncSession
    ) -> DeconflictionResult:
        stmt = select(FlightDeclarationORM).where(
            FlightDeclarationORM.start_datetime < request.end_datetime,
            FlightDeclarationORM.end_datetime > request.start_datetime,
            FlightDeclarationORM.state.in_(_ACTIVE_STATES),
        )
        if request.declaration_id:
            stmt = stmt.where(FlightDeclarationORM.id != request.declaration_id)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        ...
```

```bash
export FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE=flight_blender.plugins.examples.hello_world_engine.HelloWorldEngine
```

### Traffic Data Fuser — `hello_world_fuser.py`

Groups raw observations by ICAO address, drops stale data (older than 60 s), and emits one `TrackMessage` per aircraft using the most recent observation. This de-duplicates multiple reports for the same aircraft and always presents the newest position.

```python
class HelloWorldFuser:
    def __init__(self, session_id: str, raw_observations: list):
        self.session_id = session_id
        self.raw_observations = raw_observations

    def generate_track_messages(self) -> list[TrackMessage]:
        # Group by ICAO, keep latest observation per aircraft, drop stale
        ...
```

The constructor receives `session_id` and `raw_observations` — the same arguments the framework passes when instantiating any traffic data fuser.

```bash
export FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER=flight_blender.plugins.examples.hello_world_fuser.HelloWorldFuser
```

### Volume 4D Generator — `hello_world_volume_generator.py`

Splits the overall time window across GeoJSON features proportionally to each segment's geodesic length, producing time-sequenced volumes that approximate actual UAV transit. Each feature's geometry is buffered by ~55 m for a safety margin.

```python
class HelloWorldVolumeGenerator:
    def __init__(
        self,
        default_uav_speed_m_per_s,
        default_uav_climb_rate_m_per_s,
        default_uav_descent_rate_m_per_s,
    ): ...

    def build_v4d_from_geojson(
        self, geo_json_fc, start_datetime, end_datetime
    ) -> list[Volume4D]:
        # Compute per-feature geodesic length, proportion time, buffer geometry
        ...
```

The constructor receives UAV performance parameters. The framework calls `build_v4d_from_geojson()` with a GeoJSON FeatureCollection and a time window.

```bash
export FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR=flight_blender.plugins.examples.hello_world_volume_generator.HelloWorldVolumeGenerator
```

### Built-in Advanced Example

The project also includes an altitude-aware de-confliction engine at `src/flight_blender/plugins/examples/altitude_aware_deconfliction_engine.py` that demonstrates more advanced patterns:

```bash
export FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE=flight_blender.plugins.examples.altitude_aware_deconfliction_engine.AltitudeAwareDeconflictionEngine
```

## Testing Your Plugin

You can test that `load_plugin` accepts your class without running the full server:

```python
from flight_blender.plugins.loader import load_plugin
from flight_blender.domain_types.plugin_protocols import DeconflictionEngineProtocol

# This will raise TypeError if the class doesn't satisfy the protocol
MyEngine = load_plugin(
    "flight_blender.plugins.examples.hello_world_engine.HelloWorldEngine",
    expected_protocol=DeconflictionEngineProtocol,
)
print(
    f"Loaded: {MyEngine}"
)  # <class 'flight_blender.plugins.examples.hello_world_engine.HelloWorldEngine'>
```

Write pytest tests to verify behavior. The deconfliction engine is async and receives an `AsyncSession`, so use `AsyncMock` for the database:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
from flight_blender.domain_types.flight_declarations import (
    DeconflictionRequest,
    DeconflictionResult,
)
from flight_blender.plugins.examples.hello_world_engine import HelloWorldEngine


class TestHelloWorldEngine:
    @pytest.mark.asyncio
    async def test_approves_when_no_conflicts(self):
        """With an empty database, every declaration is approved."""
        engine = HelloWorldEngine()
        now = datetime.now(tz=timezone.utc)
        request = DeconflictionRequest(
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            view_box=[0.0, 0.0, 1.0, 1.0],
            ussp_network_enabled=0,
        )
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result
        result = await engine.check_deconfliction(request, db=mock_db)
        assert isinstance(result, DeconflictionResult)
        assert result.is_approved is True
        assert result.all_relevant_declarations == []
```

## Checklist

- [ ] Your class implements all methods defined in the protocol
- [ ] Method signatures match (argument names, types, return type) — remember `check_deconfliction` is `async` and takes `db: AsyncSession`
- [ ] Your module is importable (on the Python path, has `__init__.py` where needed)
- [ ] The environment variable is set to the full dotted path: `package.module.ClassName`
- [ ] You've tested with `load_plugin()` that the class loads without errors
