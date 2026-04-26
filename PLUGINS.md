# Writing Plugins for Flight Blender

Flight Blender uses a plugin system that lets you replace core components with your own implementations — no forking required. This guide walks through the architecture and shows you how to build a plugin from scratch.

## How It Works

The plugin system is built on three concepts:

1. **Protocols** — Python `typing.Protocol` classes that define the method signatures your plugin must implement (structural subtyping, no inheritance required).
2. **Dotted-path settings** — Each extension point has a Django setting (backed by an environment variable) that holds the fully qualified class path of the plugin to load.
3. **`load_plugin()`** — A loader function that imports the class, validates it against the protocol, caches it, and returns it to the caller.

```
Environment variable
    └─▶ Django setting (dotted class path)
            └─▶ load_plugin()  ──▶  import + validate + cache
                                         └─▶  caller instantiates your class
```

## Available Extension Points

| Extension Point | Environment Variable | Protocol | Default Implementation |
|---|---|---|---|
| De-confliction engine | `FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE` | `DeconflictionEngine` | `flight_declaration_operations.deconfliction_engine.DefaultDeconflictionEngine` |
| Traffic data fuser | `FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER` | `TrafficDataFuser` | `surveillance_monitoring_operations.utils.TrafficDataFuser` |
| Volume 4D generator | `FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR` | _(none)_ | _(empty — disabled by default)_ |

## Quick Start

The `example_plugins/` directory ships with a working example for every extension point. To try one out:

### 1. Pick a plugin

The project includes these ready-to-use examples:

| Example | File | What it does |
|---|---|---|
| De-confliction engine | `example_plugins/hello_world_engine.py` | Rejects declarations that overlap existing accepted flights by time window |
| Traffic data fuser | `example_plugins/hello_world_fuser.py` | De-duplicates observations per aircraft, drops stale data, emits latest position |
| Volume 4D generator | `example_plugins/hello_world_volume_generator.py` | Splits the time window across features proportionally to segment length |

### 2. Point the setting to the class

```bash
export FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE=example_plugins.hello_world_engine.HelloWorldEngine
```

Or add it to your `.env` file if you use one.

### 3. Start Flight Blender

Launch as normal. The framework loads your class automatically and calls it in place of the default.

### Writing your own

Create a file anywhere on the Python path. Your class only needs to implement the method(s) defined by the protocol — no inheritance required. Here's the minimal skeleton for a de-confliction engine:

```python
# my_plugins/my_engine.py

from flight_declaration_operations.data_definitions import (
    DeconflictionRequest,
    DeconflictionResult,
)


class MyEngine:
    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
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
# flight_declaration_operations/deconfliction_protocol.py


@runtime_checkable
class DeconflictionEngine(Protocol):
    def check_deconfliction(
        self, request: DeconflictionRequest
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
# surveillance_monitoring_operations/traffic_data_fuser_protocol.py


@runtime_checkable
class TrafficDataFuser(Protocol):
    def generate_track_messages(self) -> list[TrackMessage]: ...
```

Traffic data fusers are instantiated with `(session_id: str, raw_observations: list)`. The optional `BaseTrafficDataFuser` base class in `common/base_traffic_data_fuser.py` provides helper methods for speed/bearing calculation and track message generation — you can extend it for convenience, but it's not required.

## Example Plugins

The `example_plugins/` directory ships with a working example for **every** extension point. Each can be activated with a single environment variable.

### De-confliction Engine — `hello_world_engine.py`

Rejects flight declarations whose time window overlaps an existing accepted declaration. Uses a simple database query (no spatial indexing) — easy to understand and extend with your own conflict rules.

```python
class HelloWorldEngine:
    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        overlapping = FlightDeclaration.objects.filter(
            start_datetime__lt=request.end_datetime,
            end_datetime__gt=request.start_datetime,
            state__in=[_STATE_ACCEPTED, _STATE_ACCEPTED_WITH_CONDITIONS],
        )
        if request.declaration_id:
            overlapping = overlapping.exclude(pk=request.declaration_id)
        ...
```

```bash
export FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE=example_plugins.hello_world_engine.HelloWorldEngine
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
export FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER=example_plugins.hello_world_fuser.HelloWorldFuser
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
export FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR=example_plugins.hello_world_volume_generator.HelloWorldVolumeGenerator
```

### Built-in Advanced Example

The project also includes an altitude-aware de-confliction engine at `flight_declaration_operations/example_deconfliction_engine.py` that demonstrates more advanced patterns:

```bash
export FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE=flight_declaration_operations.example_deconfliction_engine.AltitudeAwareDeconflictionEngine
```

## Testing Your Plugin

You can test that `load_plugin` accepts your class without running the full server:

```python
from common.plugin_loader import load_plugin
from flight_declaration_operations.deconfliction_protocol import DeconflictionEngine

# This will raise TypeError if the class doesn't satisfy the protocol
MyEngine = load_plugin(
    "example_plugins.hello_world_engine.HelloWorldEngine",
    expected_protocol=DeconflictionEngine,
)
print(
    f"Loaded: {MyEngine}"
)  # <class 'example_plugins.hello_world_engine.HelloWorldEngine'>
```

Write a Django `TestCase` to verify behavior (the example engine queries the database, so use `TestCase` instead of `SimpleTestCase`):

```python
from django.test import TestCase
from datetime import datetime, timezone, timedelta
from flight_declaration_operations.data_definitions import (
    DeconflictionRequest,
    DeconflictionResult,
)
from example_plugins.hello_world_engine import HelloWorldEngine


class HelloWorldEngineTests(TestCase):
    def test_approves_when_no_conflicts(self):
        """With an empty database, every declaration is approved."""
        engine = HelloWorldEngine()
        now = datetime.now(tz=timezone.utc)
        request = DeconflictionRequest(
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            view_box=[0.0, 0.0, 1.0, 1.0],
            ussp_network_enabled=0,
        )
        result = engine.check_deconfliction(request)
        self.assertIsInstance(result, DeconflictionResult)
        self.assertTrue(result.is_approved)
        self.assertEqual(result.all_relevant_declarations, [])
```

## Checklist

- [ ] Your class implements all methods defined in the protocol
- [ ] Method signatures match (argument names, types, return type)
- [ ] Your module is importable (on the Python path, has `__init__.py` where needed)
- [ ] The environment variable is set to the full dotted path: `package.module.ClassName`
- [ ] You've tested with `load_plugin()` that the class loads without errors
