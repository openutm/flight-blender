# Flight Blender

![blender-logo](images/blender-logo.jpg)

Flight Blender is an open-source backend and data-processing engine designed to support standards-compliant UTM (Unmanned Traffic Management) services. It adheres to the latest regulations for UTM/U-Space in the EU and other jurisdictions. With Flight Blender, you can:

- Implement a Remote ID "service provider" compatible with the ASTM-F3411 Remote ID standard, along with Flight Spotlight, an open-source Remote ID Display Application.
- Use an open-source implementation of the ASTM F3548 USS-to-USS standard, compatible with EU U-Space regulations for flight authorization.
- Interact with interoperability software like `interuss/dss` to exchange data with other UTM systems.
- Process geo-fences using the ED-269 standard.
- Monitor conformance and send operator notifications.
- Aggregate flight traffic feeds from various sources, including geo-fences, flight declarations, and air-traffic data.

## Key Features

### DSS Connectivity
Connect and retrieve data such as Remote ID information or perform strategic deconfliction and flight authorization.

### Flight Tracking
Ingest flight tracking feeds from sources like ADS-B, live telemetry, and Broadcast Remote ID. Outputs a unified JSON feed for real-time display.

### Geofence Management
Submit geofences to Flight Blender, which can then be transmitted to Spotlight.

### Flight Declaration
Submit future flight plans (up to 24 hours in advance) using the ASTM USS-to-USS API or as a standalone component. Supported DSS APIs are listed below.

### Network Remote ID
Compliant with ASTM standards, this module can act as a "display provider" or "service provider" for Network Remote ID.

### Operator Notifications
Send notifications to operators using an AMQP queue.

### Conformance Monitoring (Beta)
Monitor flight paths against declared 4D volumes for conformance.

---

## ▶️ Get Started in 20 Minutes

Follow our simple 5-step guide to deploy Flight Blender and explore its core features.

📖 [Read the 20-minute quickstart guide](deployment_support/README.md) to get started now!

---
## 💫 Join the community 
[Discord](https://discord.gg/dnRxpZdd9a)

---

## Technical Resources

- **API Specification**: Explore the [API documentation](http://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/openutm/flight-blender/master/api/flight-blender-server-1.0.0-resolved.yaml) to understand available endpoints and data interactions.
- **Flight Tracking Data**: Review [sample flight tracking data](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/air_traffic_samples/micro_flight_data_single.json) and the [Air-traffic Data Protocol](https://github.com/openskies-sh/airtraffic-data-protocol-development/blob/master/Airtraffic-Data-Protocol.md).

---

## Submitting Data to Flight Blender

Here are examples of the types of data you can submit to Flight Blender:

- **Area of Interest (AOI)**: [Sample AOI GeoJSON](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/aoi_geo_fence_samples/aoi.geojson)
- **Geofence**: [Sample Geofence GeoJSON](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/aoi_geo_fence_samples/geo_fence.geojson). Includes converters for EuroCAE ED-269 standard.
- **Flight Declaration**: [Sample Flight Declaration](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/flight_declarations_samples/flight-1-bern.json). This follows the [Flight Declaration Protocol](https://github.com/openskies-sh/flight-declaration-protocol-development) and supports "operational intent" APIs when using DSS components.

---

Flight Blender is your gateway to building robust, standards-compliant UTM services. Start exploring today!
