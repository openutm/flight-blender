from dataclasses import asdict
from typing import List

import arrow
from pyproj import Geod

from common.base_traffic_data_fuser import BaseTrafficDataFuser
from common.redis_stream_operations import RedisStreamOperations
from flight_feed_operations.data_definitions import SingleAirtrafficObservation

from .data_definitions import ActiveTrack


class TrafficDataFuser (BaseTrafficDataFuser):
    """A default data fuser to generate track messages"""

    def __init__(self, session_id: str, raw_observations: List[SingleAirtrafficObservation]):
        self.raw_observations = raw_observations
        self.SDSP_IDENTIFIER = "SDSP123"
        self.redis_stream_helper = RedisStreamOperations()
        self.session_id = session_id
        self.geod = Geod(ellps="WGS84")

    def _fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        # No fusing is actually done, this just returns the data that is in the redis streams.
        return self.raw_observations

    def _generate_active_tracks(self):
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
