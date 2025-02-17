# Flight Blender

![blender-logo](images/blender-logo.jpg)

Flight Blender is the UTM data processing engine for the OpenUTM stack. 

Argon server is a backend / data-processing engine to stand up standards-compliant UTM services that adhere to the latest regulations on UTM / U-Space in the EU and other jurisdictions. Specifically, it gives you:

- an open source Remote ID "service provider" compatible with ASTM Remote ID standard, it comes with Flight Spotlight a opensource remote ID Display Application as well.
- an open source implementation of the ASTM USS <-> USS standard and compatible with the EU U-Space regulation for flight authorisation
- ability to interact with `interuss/dss` or similar interoperability software to exchange data with other UTM implementations
- ability to consume geo-fences per the ED-269 standard
- basic monitoring of conformance and operator notifications
- a flight traffic feed aggregator that has different modules that can process and relay data around flights and airspace: geo-fence, flight declarations, air-traffic data.

There are different modules that enable this:
- _DSS Connectivity_: There are modules to connect and read for e.g. Remote ID data from a DSS, Strategic deconfliction / flight authorization
- _Flight Tracking_: It takes in flight tracking feeds from various sources e.g. ADS-B, live telemetry, Broadcast Remote ID and others and outputs as a single fused JSON feed and submits it to a Display Application to be shown in real-time on a display
- _Geofence_: A Geofence can be submitted into Flight Blender and consequently transmitted to Spotlight
- _Flight Declaration_: Future flights up-to 24 hours can be submitted, this support both the ASTM USS <-> USS API and can also be used as a standalone component, for supported DSS APIs see below
- _Network Remote-ID_ : The Network RID module is compliant with ASTM standards for Network RID and can be used as a "display provider" or as a "service provider"
- _Operator Notifications_: Using a AMQP queue you can send notifications to the operator
- _Conformance Monitoring_ (beta): Monitory trajectory / flight path against the declared 4D Volume

## ▶️ Get started in 20 mins
Follow our 5-step process to deploy Flight Blender and get started with the basic concepts of the software.

Read the ⏲️ [20-minute quickstart](deployment_support/README.md) now!

## Technical details

- To begin, review the [API Specification](http://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/openutm/flight-blender/master/api/flight-blender-1.0.0-resolved.yaml) to understand the endpoints and the kind of data that you can set in Flight Blender.
- Then take a look at some data formats: [Flight tracking data](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/air_traffic_samples/micro_flight_data_single.json). This file follows the format as specified in the [Air-traffic data protocol](https://github.com/openskies-sh/airtraffic-data-protocol-development/blob/master/Airtraffic-Data-Protocol.md)

## Submitting AOI, Flight Declarations and Geofence data

Take a look at sample data below to see the kind of data that can be submitted in Flight Blender

- [Area of Interest](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/aoi_geo_fence_samples/aoi.geojson) as a GeoJSON
- [Geofence](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/aoi_geo_fence_samples/geo_fence.geojson) as a GeoJSON, we have converters to convert EuroCAE from ED-269 standard
- [Flight Declaration](https://github.com/openutm/verification/blob/main/flight_blender_e2e_integration/flight_declarations_samples/flight-1-bern.json). This file follows the format specified in [Flight Declaration Protocol](https://github.com/openskies-sh/flight-declaration-protocol-development), optionally when using DSS components it supports "operational intent" APIs.
