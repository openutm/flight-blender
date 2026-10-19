# Flight Blender GCS Connector Implementation Plan

## Purpose

Implement a maintainable, releasable, testable, and end-user-friendly integration between Flight Blender and ground control stations, starting with Mission Planner and retaining a low-duplication path to QGroundControl.

This document is intended to be executable by local coding agents. It defines architecture, sequencing, constraints, acceptance criteria, and explicit non-goals.

## Governing repository rules

All implementation work in this repository must follow `AGENTS.md`. Where this plan conflicts with `AGENTS.md`, `AGENTS.md` takes precedence.

The connector work should not weaken Flight Blender's existing architecture, import hierarchy, authentication enforcement, transaction boundaries, or test requirements.

## Product decision

The primary product is a standalone, cross-platform **Flight Blender GCS Connector**. Ground-control-station integrations are thin adapters.

The first polished end-user integration is Mission Planner because it supports runtime plugins. The first development environment is macOS, so all Flight Blender-specific business logic must remain outside the Windows-only Mission Planner plugin.

QGroundControl support will reuse the same connector protocol and domain model. Native QGC integration is deferred until companion-mode feedback justifies owning a custom QGC distribution.

## Goals

1. Allow an operator to submit a GCS mission to Flight Blender as a flight declaration.
2. Stream live aircraft telemetry from the GCS to Flight Blender.
3. Display declaration, authorization, telemetry, conformance, and connector status clearly.
4. Keep Flight Blender API logic, authentication, geometry, state management, diagnostics, and detailed UI in one reusable connector.
5. Make daily core development possible on macOS.
6. Release Mission Planner support as one simple Windows installer.
7. Avoid forks or modifications to Mission Planner and QGroundControl upstream source repositories.
8. Minimize duplicated code across Mission Planner and QGroundControl.

## Non-goals for the first release

- Native QGroundControl UI integration.
- Mobile support.
- Multi-aircraft fleet management.
- Detailed trajectory timing for every mission segment.
- Native traffic overlays in both GCS applications.
- Long-duration offline telemetry replay.
- Independent auto-updaters for connector and adapters.
- Supporting every possible OAuth provider before the target deployment is known.

## Architecture

```text
Mission Planner bridge                  QGC adapter later
Windows, .NET Framework                 C++/QML or companion
          |                                      |
          +---------- versioned local API -------+
                              |
                              v
              Flight Blender GCS Connector
              modern cross-platform runtime
              macOS / Windows / Linux

              - operation state machine
              - Flight Blender API gateway
              - authentication
              - mission conversion
              - telemetry pipeline
              - persistence
              - diagnostics
              - local web UI
                              |
                              v
                     Flight Blender API
```

## Mandatory architectural constraints

### Connector process boundary

The connector must be a separate process from the first production-quality implementation.

Do not place the following inside the Mission Planner plugin:

- generated Flight Blender API client;
- OAuth/token refresh logic;
- mission geometry generation;
- telemetry retry logic;
- operation state machine;
- detailed diagnostics;
- detailed configuration UI;
- Flight Blender compatibility handling.

### Thin adapters

A GCS adapter may only:

1. start or locate the connector;
2. report GCS and adapter versions;
3. extract a neutral mission snapshot;
4. report vehicle identity and connection state;
5. sample and send telemetry;
6. forward operator commands;
7. display compact status and critical warnings;
8. open the connector web UI.

### Language-neutral local contract

Do not share compiled DTO assemblies between the modern connector, the Mission Planner `net472` plugin, and QGC.

Define a versioned JSON/HTTP contract under `contracts/`. Use JSON Schema or OpenAPI as the source of truth.

### Single authoritative state machine

Only the connector may interpret Flight Blender lifecycle responses and decide operation state. Adapters and web pages consume normalized connector status.

### Single detailed UI

The connector hosts the detailed configuration, operation preview, status, and diagnostics interface as a local web application. Adapters provide only compact native integration.

Open the local UI in the system browser for the first release. Do not embed a browser runtime in Mission Planner initially.

## Proposed repository structure

The connector should initially live in a dedicated repository rather than being embedded in Flight Blender server source. Until that repository exists, keep design artifacts in Flight Blender under documentation only.

Recommended connector repository:

```text
flight-blender-gcs/
├── src/
│   ├── Connector/
│   │   ├── Application/
│   │   ├── Domain/
│   │   ├── FlightBlender/
│   │   ├── Geometry/
│   │   ├── Telemetry/
│   │   ├── Persistence/
│   │   └── Web/
│   └── Adapters/
│       ├── MissionPlanner/
│       └── QGroundControl/
├── contracts/
│   ├── adapter-api.yaml
│   ├── mission-snapshot.schema.json
│   ├── telemetry-sample.schema.json
│   └── connector-status.schema.json
├── vendor/
│   └── flight-blender-openapi/
├── tests/
│   ├── Unit/
│   ├── Contract/
│   ├── Integration/
│   ├── GoldenFiles/
│   ├── MissionPlannerHarness/
│   └── EndToEnd/
├── packaging/
│   ├── windows/
│   └── macos/
└── tools/
```

## Local adapter API

Initial API surface:

```http
POST /v1/adapters/register
PUT  /v1/adapters/{adapterId}/mission
PUT  /v1/adapters/{adapterId}/vehicle
POST /v1/adapters/{adapterId}/telemetry
POST /v1/adapters/{adapterId}/commands
GET  /v1/status
GET  /v1/events
```

Use server-sent events for connector-to-adapter status updates initially. Do not introduce WebSockets unless bidirectional streaming becomes necessary.

Every adapter request must include:

- schema version;
- adapter name and version;
- GCS name and version;
- adapter instance ID;
- UTC timestamp;
- explicit units;
- explicit altitude reference;
- correlation ID where applicable.

Example telemetry payload:

```json
{
  "schemaVersion": "1.0",
  "source": {
    "adapter": "mission-planner",
    "adapterVersion": "0.1.0",
    "gcsVersion": "unknown",
    "instanceId": "00000000-0000-0000-0000-000000000000"
  },
  "observedAt": "2026-08-04T09:28:21.412Z",
  "aircraftId": "HU-EXAMPLE-01",
  "position": {
    "latitudeDeg": 47.123,
    "longitudeDeg": 19.456,
    "altitude": {
      "valueM": 182.4,
      "reference": "amsl"
    }
  }
}
```

## Connector modules

### Domain

Define GCS-neutral objects for:

- mission snapshot;
- route and waypoint;
- altitude value and datum;
- vehicle identity;
- telemetry sample;
- operation identity;
- declaration state;
- authorization state;
- telemetry state;
- conformance state;
- connector health;
- normalized notification.

Domain types must not reference Mission Planner, QGC, generated OpenAPI models, HTTP clients, UI types, or persistence classes.

### Flight Blender gateway

Generate a low-level API client from a pinned Flight Blender OpenAPI specification. Check generated sources into the connector repository.

Store:

```text
vendor/flight-blender-openapi/
├── specification.yaml
├── source-commit.txt
├── specification.sha256
└── generated/
```

Rules:

- Never generate during a normal build.
- Never fetch the specification during a release build.
- Wrap generated classes behind a hand-written gateway.
- Do not expose generated request or response classes outside the gateway module.
- Authentication scopes, audience, token endpoint, and provider remain deployment-profile settings.

Gateway interface:

```csharp
public interface IFlightBlenderGateway
{
    Task<ServerCapabilities> GetCapabilitiesAsync(CancellationToken cancellationToken);
    Task<DeclarationResult> CreateDeclarationAsync(
        FlightDeclaration declaration,
        CancellationToken cancellationToken);
    Task<DeclarationStatus> GetDeclarationAsync(
        DeclarationId declarationId,
        CancellationToken cancellationToken);
    Task SubmitTelemetryAsync(
        IReadOnlyList<TelemetryObservation> observations,
        CancellationToken cancellationToken);
    Task CancelDeclarationAsync(
        DeclarationId declarationId,
        CancellationToken cancellationToken);
}
```

### Mission conversion

First implementation: buffered route corridor.

Inputs:

- home position;
- waypoints;
- altitude and altitude reference;
- takeoff and landing positions;
- planned time window;
- expected duration;
- vehicle identity and type.

Configurable defaults:

- horizontal buffer;
- vertical buffer;
- pre-flight time buffer;
- post-flight time buffer.

The preview must show the source altitude, converted altitude, datum, and all assumptions. Never silently infer an altitude datum.

Detailed per-segment 4D timing is deferred.

### Telemetry pipeline

Recommended initial behavior:

- sample at 2-5 Hz;
- upload at 1 Hz;
- use a small bounded queue;
- coalesce stale intermediate positions;
- preserve lifecycle transitions;
- reject observations older than a configured maximum age;
- never block a GCS callback or UI thread;
- expose last successful upload and queue depth.

Only one adapter may own active telemetry for an aircraft at a time.

### Operation ownership

Operation ownership key:

```text
deployment profile
+ aircraft identity
+ operation identity
+ adapter instance
```

If another adapter attempts to control an active operation, require explicit transfer.

### Operation state machine

Minimum states:

```text
NoMission
MissionAvailable
DraftValidated
DeclarationSubmitting
DeclarationAccepted
AwaitingAuthorization
Authorized
TelemetryStarting
Active
Completing
Completed
Failed
Cancelled
```

Keep these statuses independently visible:

- mission state;
- declaration state;
- DSS submission state;
- authorization state;
- telemetry state;
- conformance state;
- connector health;
- Flight Blender reachability.

Do not collapse them into one readiness flag.

## Mission Planner adapter

### Runtime constraints

Mission Planner currently uses .NET Framework and Windows Forms. Treat Windows as the authoritative runtime and test environment.

Core development remains on macOS. The Mission Planner bridge must be testable beneath a small facade without launching Mission Planner.

### Structure

```text
MissionPlannerPluginEntry
        |
        v
IMissionPlannerFacade
        |
        v
MissionPlannerAdapter
        |
        v
Local connector protocol
```

Only the plugin entry point and facade implementation may reference Mission Planner types.

### Plugin responsibilities

- locate/start connector;
- handshake protocol compatibility;
- send mission snapshot;
- send vehicle identity;
- enqueue telemetry without blocking;
- show compact status;
- show critical warnings;
- open connector web UI;
- cleanly detach on unload.

### Build strategy

Do not build against a Mission Planner source checkout via project reference.

CI should:

1. download a pinned Mission Planner release;
2. verify checksum;
3. extract reference assemblies;
4. compile the plugin with copy-local disabled for Mission Planner assemblies;
5. package only required plugin files;
6. run a Windows load smoke test against each supported Mission Planner release.

Publish a tested version range rather than claiming universal compatibility.

## QGroundControl strategy

### First implementation

Support stock QGC through companion mode:

- import `.plan` files;
- receive MAVLink through UDP forwarding;
- reuse connector UI;
- submit declarations;
- stream telemetry;
- display status in the connector interface.

### Native integration decision gate

Do not create a custom QGC distribution until at least one condition is met:

- companion workflow is rejected by target users;
- native map overlays are operationally required;
- the project has capacity to own QGC builds, signing, installers, regression tests, and security updates;
- QGC exposes a suitable external extension mechanism.

Any later native QGC adapter must remain thin and use the same local connector contract.

## User experience

### Mission Planner installation

Release one installer containing:

```text
FlightBlenderForMissionPlanner-<version>.exe
├── Mission Planner bridge
├── Flight Blender Connector
├── connector web UI
├── deployment profile support
└── uninstaller
```

The user must not install or manage the connector separately.

### First run

1. Install package.
2. Restart Mission Planner.
3. Click `Flight Blender`.
4. Connector starts automatically.
5. Browser opens local setup page.
6. Select or enter deployment profile.
7. Authenticate.
8. Run connection test.
9. Return to Mission Planner and plan normally.

### Normal operation

1. Create/load mission.
2. Click `Check and submit flight`.
3. Review route, altitude, and time assumptions.
4. Submit declaration.
5. Confirm authorization status.
6. Start operation.
7. Fly normally.
8. Complete operation.

No JSON editing, token copying, command-line scripts, or manually started background services should be required for production users.

## Security

- Bind connector API to loopback only.
- Authenticate local adapter connections with an installation secret or operating-system IPC protection.
- Store production credentials using the operating-system credential store.
- Never write refresh tokens to plain configuration files.
- Redact tokens, secrets, and sensitive headers from logs and support bundles.
- Require explicit configuration for development authentication bypass.
- Do not treat a successful declaration as legal authorization to fly.

## Testing strategy

### Unit tests

Inject and fake:

- clock;
- UUID generator;
- Flight Blender gateway;
- credential provider;
- persistence;
- telemetry sink;
- event publisher.

Test:

- state transitions;
- mission conversion;
- altitude conversion;
- route buffering;
- retries;
- telemetry coalescing;
- stale observation handling;
- operation ownership;
- duplicate command handling;
- error normalization;
- secret redaction.

### Golden mission tests

For equivalent missions exported from Mission Planner and QGC, verify equivalent normalized declarations.

```text
tests/GoldenFiles/missions/
├── simple-square/
│   ├── mission-planner-input.json
│   ├── qgc-input.json
│   └── expected-declaration.json
└── terrain-following/
```

### Property-based geometry tests

Verify:

- every waypoint is covered by its corridor;
- altitude upper bound is never below lower bound;
- time intervals never run backwards;
- polygons are valid;
- latitude and longitude are never swapped;
- output is deterministic;
- splitting segments does not unexpectedly reduce coverage.

### Contract tests

Two levels:

1. mock Flight Blender server for fast edge-case tests;
2. real Flight Blender deployment for compatibility tests.

Reuse the OpenUTM verification toolkit for end-to-end Flight Blender verification where practical. Do not duplicate existing conformance scenarios unnecessarily.

### Mission Planner tests

- facade and adapter unit tests run on macOS;
- Windows CI compiles the plugin;
- Windows smoke test loads plugin into supported Mission Planner releases;
- SITL test verifies mission extraction and telemetry flow;
- full UI automation is optional and should only be added where stable.

### End-to-end scenario

1. Start Flight Blender test deployment.
2. Start connector.
3. Start ArduPilot SITL.
4. Start Mission Planner on Windows.
5. Load known mission.
6. Submit declaration.
7. Confirm declaration exists in Flight Blender.
8. Start flight.
9. Verify telemetry reaches Flight Blender.
10. Introduce route deviation.
11. Verify conformance/notification reaches connector.
12. Land.
13. Complete operation.
14. Verify clean final state and sanitized report.

## macOS development workflow

Daily native work:

- connector development;
- web UI development;
- unit tests;
- geometry tests;
- contract tests;
- Flight Blender in Docker Compose;
- ArduPilot SITL;
- generic MAVLink input;
- later QGC adapter development.

Windows-only work:

- Mission Planner runtime debugging;
- plugin loading;
- WinForms integration;
- installer testing;
- code signing;
- final SITL acceptance.

Recommended environment:

```text
macOS host
├── connector development natively
├── Flight Blender via Docker Compose
├── QGC development natively later
├── Windows GitHub Actions build
└── Windows 11 VM for Mission Planner debugging
```

A successful macOS compilation of the Mission Planner plugin is not release validation. Windows CI and runtime smoke tests are authoritative.

## Release policy

### Versioning

Version connector and adapter contract explicitly.

Handshake example:

```json
{
  "protocolVersion": "1.0",
  "adapterVersion": "1.0.0",
  "connectorMinimum": "1.0.0",
  "connectorMaximumExclusive": "2.0.0"
}
```

### Atomic updates

For initial releases, update the Mission Planner bridge and connector together through one installer. Do not add independent auto-update channels until compatibility behavior is proven.

### Channels

- internal;
- preview;
- stable.

### Release gates

A stable Mission Planner release requires:

- all unit and contract tests passing;
- real Flight Blender integration tests passing;
- Mission Planner load smoke tests passing for supported versions;
- end-to-end SITL scenario passing;
- installer upgrade test passing;
- uninstall test passing;
- credential redaction test passing;
- signed artifacts;
- compatibility matrix updated;
- user documentation updated.

## Implementation phases

### Phase 0 — Boundary prototypes

Tasks:

- [ ] Confirm target Flight Blender deployment and authentication flow.
- [ ] Pin the exact Flight Blender API specification and commit.
- [ ] Submit one declaration through a minimal test client.
- [ ] Send one telemetry stream through a minimal test client.
- [ ] Retrieve and normalize declaration status.
- [ ] Extract one Mission Planner mission on Windows.
- [ ] Sample Mission Planner vehicle state on Windows.
- [ ] Receive MAVLink and parse one QGC `.plan` file on macOS.
- [ ] Record all unresolved API/schema/authentication mismatches.

Exit criteria:

- Every external boundary works independently.
- Authentication configuration is proven against the intended deployment.
- No polished UI or installer is required.

### Phase 1 — Connector foundation

Tasks:

- [ ] Create connector repository and solution.
- [ ] Define adapter API and JSON Schemas.
- [ ] Define neutral domain model.
- [ ] Implement operation state machine.
- [ ] Implement pinned Flight Blender gateway.
- [ ] Implement deployment profiles.
- [ ] Implement bounded telemetry pipeline.
- [ ] Implement local persistence.
- [ ] Implement structured logging and redaction.
- [ ] Implement basic local web UI.
- [ ] Add unit, property, golden, and contract tests.
- [ ] Add macOS and Windows CI jobs.

Exit criteria:

- A simulator or CLI adapter can complete an operation without Mission Planner or QGC.
- Core tests run natively on macOS.

### Phase 2 — Mission Planner bridge

Tasks:

- [ ] Define `IMissionPlannerFacade`.
- [ ] Implement facade with real Mission Planner types.
- [ ] Implement connector process discovery/startup.
- [ ] Implement protocol handshake.
- [ ] Implement mission extraction.
- [ ] Implement vehicle identity extraction.
- [ ] Implement non-blocking telemetry queue.
- [ ] Add compact status UI.
- [ ] Add critical warnings.
- [ ] Add `Open Flight Blender` action.
- [ ] Add Windows build using pinned release assemblies.
- [ ] Add adapter unit tests and Windows load smoke test.

Exit criteria:

- A Mission Planner SITL flight works end to end.
- No Flight Blender API logic exists in the plugin.

### Phase 3 — Releasable Mission Planner product

Tasks:

- [ ] Implement first-run setup.
- [ ] Implement production credential storage.
- [ ] Add mission-changed-since-declaration detection.
- [ ] Add reconnection behavior.
- [ ] Add operation ownership conflict handling.
- [ ] Add support bundle export.
- [ ] Build one Windows installer.
- [ ] Add upgrade and uninstall tests.
- [ ] Add code signing.
- [ ] Publish compatibility matrix.
- [ ] Write end-user documentation.

Exit criteria:

- A non-developer can install and complete a test operation without command-line work.

### Phase 4 — QGC companion compatibility

Tasks:

- [ ] Import QGC `.plan` files.
- [ ] Receive MAVLink through UDP forwarding.
- [ ] Normalize QGC mission and telemetry into existing contracts.
- [ ] Reuse the connector web UI and state model.
- [ ] Add QGC setup documentation.
- [ ] Add golden equivalence tests against Mission Planner missions.
- [ ] Add macOS QGC/SITL acceptance scenario.

Exit criteria:

- Stock QGC can complete the same Flight Blender workflow without a custom QGC binary.

### Phase 5 — Native QGC decision

Tasks:

- [ ] Collect user feedback from companion mode.
- [ ] Identify native features that are operationally necessary.
- [ ] Estimate ownership cost of custom QGC releases.
- [ ] Check for new upstream extension points.
- [ ] Decide whether to remain companion-only, add a custom build, or pursue upstream changes.

Exit criteria:

- Written product decision approved before native QGC implementation begins.

## Agent execution rules

Local coding agents working from this plan must:

1. Read `AGENTS.md` before editing Flight Blender.
2. Work on one phase or one explicitly scoped task at a time.
3. Do not implement deferred features opportunistically.
4. Keep adapters thin and reject architecture changes that move connector logic into a GCS plugin.
5. Add tests in the same change as implementation.
6. Record API inconsistencies rather than silently compensating without documentation.
7. Keep generated code isolated and reproducible.
8. Avoid introducing additional deployable services.
9. Avoid introducing a frontend framework unless the current UI approach demonstrably cannot meet requirements.
10. Update this plan's checkboxes and decision log as work progresses.

## Decision log

Record significant decisions here or in linked ADRs.

| Date | Decision | Reason | Status |
|---|---|---|---|
| 2026-08-04 | Connector is a mandatory separate process | Cross-platform development, dependency isolation, reuse, testability | Accepted |
| 2026-08-04 | Mission Planner is first polished adapter | Runtime plugin support and simpler end-user installation | Accepted |
| 2026-08-04 | QGC starts in companion mode | Avoid owning a custom QGC distribution prematurely | Accepted |
| 2026-08-04 | Detailed UI lives in connector web app | Minimize WinForms/QML duplication | Accepted |
| 2026-08-04 | Adapter contract is language-neutral | Mission Planner and QGC use different runtimes/languages | Accepted |

## Definition of done for the project

The project is complete when:

- Mission Planner users install one signed package and require no manual sidecar management;
- a planned mission can be reviewed and submitted to Flight Blender;
- live telemetry is transmitted reliably without affecting GCS responsiveness;
- declaration, authorization, telemetry, and conformance states are independently visible;
- macOS supports all core development and tests;
- Windows-specific behavior is covered by CI and runtime smoke tests;
- QGC can perform the same workflow through companion mode without code duplication;
- the Flight Blender API boundary is pinned, tested, and isolated;
- upgrade, uninstall, diagnostics, credential storage, and compatibility behavior are documented and tested.
