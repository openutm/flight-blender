from dataclasses import asdict
from typing import List

import arrow
from pyproj import Geod

from common.redis_stream_operations import RedisStreamOperations
from flight_feed_operations.data_definitions import SingleAirtrafficObservation
from surveillance_monitoring_operations.data_definitions import (
    AircraftPosition,
    AircraftState,
    LatLangAltPoint,
    SpeedAccuracy,
    TrackMessage,
)

from .data_definitions import ActiveTrack


class TrafficDataFuser:
    """A default data fuser to generate track messages"""

    def __init__(self, session_id: str, raw_observations: List[SingleAirtrafficObservation]):
        self.raw_observations = raw_observations
        self.SDSP_IDENTIFIER = "SDSP123"
        self.redis_stream_helper = RedisStreamOperations()
        self.session_id = session_id
        self.geod = Geod(ellps="WGS84")

    def generate_flight_speed_bearing(self, adjacent_points: List[LatLangAltPoint], delta_time_secs: float = 1.0) -> List[float]:
        first_point = adjacent_points[0]
        second_point = adjacent_points[1]

        fwd_azimuth, back_azimuth, adjacent_point_distance_mts = self.geod.inv(first_point.lng, first_point.lat, second_point.lng, second_point.lat)

        speed_mts_per_sec = adjacent_point_distance_mts / delta_time_secs
        speed_mts_per_sec = float("{:.2f}".format(speed_mts_per_sec))

        if fwd_azimuth < 0:
            fwd_azimuth = 360 + fwd_azimuth
        if delta_time_secs == 0:
            vertical_speed_mps = 0.0
        else:
            vertical_speed_mps = (second_point.alt - first_point.alt) / delta_time_secs
        vertical_speed_mps = float("{:.2f}".format(vertical_speed_mps))

        return [speed_mts_per_sec, fwd_azimuth, vertical_speed_mps]

    def fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        # No fusing is actually done, this just returns the data that is in the redis streams.
        return self.raw_observations

    def generate_active_tracks(self):
        # This method generates active tracks for a session ID and stores it in Redis. Active tracks are objects that are being tracked for a session
        # Get all the existing tracks for this session ID from Redis
        my_redis_stream_helper = RedisStreamOperations()
        # Generate tracks from fused observations for each aircraft ICAO address
        active_tracks_in_session = {}
        for observation in self.raw_observations:
            icao_address = observation.icao_address
            if icao_address not in active_tracks_in_session:
                active_tracks_in_session[icao_address] = []
            active_tracks_in_session[icao_address].append(observation)
        # Add these observations to active tracks in Redis
        for icao_address, observations in active_tracks_in_session.items():
            # check if an active track already exists for this session ID and ICAO address
            track_for_icao_address_exists = my_redis_stream_helper.check_active_track_exists(
                session_id=self.session_id, unique_aircraft_identifier=icao_address
            )
            if track_for_icao_address_exists:
                # Update the existing active track with new observations
                existing_active_track = my_redis_stream_helper.get_active_track(session_id=self.session_id, unique_aircraft_identifier=icao_address)
                existing_active_track.observations.extend([asdict(obs) for obs in observations])
                existing_active_track.last_updated_timestamp = arrow.utcnow().isoformat()
                my_redis_stream_helper.update_active_track(session_id=self.session_id, active_track=existing_active_track)
            else:
                # Create a new active track

                active_track = ActiveTrack(
                    session_id=self.session_id,
                    unique_aircraft_identifier=icao_address,
                    last_updated_timestamp=arrow.utcnow().isoformat(),
                    observations=[asdict(obs) for obs in observations],
                )
                my_redis_stream_helper.add_active_track_to_session(session_id=self.session_id, active_track=active_track)

    def generate_track_messages(self, active_tracks: List[ActiveTrack]) -> List[TrackMessage]:
        all_track_data = []
        for track in active_tracks:
            UNIQUE_AIRCRAFT_IDENTIFIER = track.unique_aircraft_identifier
            # Get the latest observation for this active track
            fused_observations = [SingleAirtrafficObservation(**obs) for obs in track.observations]
            # For simplicity, we take the last observation as the latest
            latest_observation = fused_observations[-1]
            one_before_latest_observation = fused_observations[-2] if len(fused_observations) > 1 else latest_observation
            latest_observation_lat_lng_point = LatLangAltPoint(
                lat=latest_observation.lat_dd, lng=-latest_observation.lon_dd, alt=latest_observation.altitude_mm / 1000.0
            )
            one_before_latest_observation_lat_lng_point = LatLangAltPoint(
                lat=one_before_latest_observation.lat_dd,
                lng=-one_before_latest_observation.lon_dd,
                alt=one_before_latest_observation.altitude_mm / 1000.0,
            )
            # Calculate speed and bearing
            speed_mps, bearing_degrees, vertical_speed_mps = self.generate_flight_speed_bearing(
                adjacent_points=[
                    one_before_latest_observation_lat_lng_point,
                    latest_observation_lat_lng_point,
                ],
                delta_time_secs=(arrow.get(latest_observation.timestamp) - arrow.get(one_before_latest_observation.timestamp)).total_seconds(),
            )
            # Create AircraftPosition
            aircraft_position = AircraftPosition(
                lat=latest_observation.lat_dd,
                lng=-latest_observation.lon_dd,
                alt=latest_observation.altitude_mm,
                accuracy_h="SA1mps",
                accuracy_v="SA3mps",
                extrapolated=True,
                pressure_altitude=latest_observation.altitude_mm,
            )
            speed_accuracy = SpeedAccuracy("SA1mps")
            aircraft_state = AircraftState(
                position=aircraft_position,
                speed=speed_mps,
                track=bearing_degrees,
                vertical_speed=vertical_speed_mps,
                speed_accuracy=speed_accuracy,
            )
            track_data = TrackMessage(
                sdsdp_identifier=self.SDSP_IDENTIFIER,
                unique_aircraft_identifier=UNIQUE_AIRCRAFT_IDENTIFIER,
                state=aircraft_state,
                timestamp=arrow.utcnow().isoformat(),
                source="FusedSource",
                track_state="Active",
            )
            all_track_data.append(track_data)

        return all_track_data
