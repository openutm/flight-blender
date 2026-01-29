"""Base class for traffic data fusion operations.

This module provides an abstract base class for implementing traffic data fusion
algorithms. Data fusers process raw air traffic observations and generate track messages
for surveillance display service providers (SDSP).
"""

from abc import ABC, abstractmethod
from typing import List

import arrow

from pyproj import Geod
from surveillance_monitoring_operations.data_definitions import ActiveTrack

from common.redis_stream_operations import RedisStreamOperations
from flight_feed_operations.data_definitions import SingleAirtrafficObservation
from surveillance_monitoring_operations.data_definitions import (
    AircraftPosition,
    AircraftState,
    LatLangAltPoint,
    SpeedAccuracy,
    TrackMessage,
)


class BaseTrafficDataFuser(ABC):
    """Abstract base class for traffic data fusion implementations.
    
    This class implements a template method pattern for traffic data fusion workflows.
    It provides concrete methods for common operations (speed/bearing calculation,
    track message generation) while requiring subclasses to implement specific fusion
    algorithms and track management logic.
    
    Implementations can range from simple pass-through fusers to sophisticated
    multi-sensor fusion algorithms using Kalman filters or other estimation techniques.
    
    Attributes:
        session_id: Unique identifier for the surveillance session
        raw_observations: List of raw air traffic observations to be processed
        geod: Geodetic calculator for WGS84 ellipsoid (distance/bearing calculations)
        redis_stream_helper: Helper for Redis operations (must be initialized by subclass)
        SDSP_IDENTIFIER: Surveillance Display Service Provider identifier (must be set by subclass)
    """

    def __init__(self, session_id: str, raw_observations: List[SingleAirtrafficObservation]):
        """Initialize the traffic data fuser.
        
        Args:
            session_id: Unique identifier for the surveillance session
            raw_observations: List of raw air traffic observations from various sources
            
        Note:
            Subclasses must initialize redis_stream_helper and SDSP_IDENTIFIER attributes.
        """
        self.session_id = session_id
        self.raw_observations = raw_observations
        self.geod = Geod(ellps="WGS84")
        self.redis_stream_helper: RedisStreamOperations
        self.SDSP_IDENTIFIER: str


    def generate_track_messages(self) -> List[TrackMessage]:
        """Orchestrate the complete data fusion and track generation workflow.
        
        This template method coordinates the entire fusion process by calling:
        1. _pre_process_raw_data() - Optional pre-processing hook
        2. _fuse_raw_observations() - Abstract method for sensor fusion
        3. _generate_active_tracks() - Abstract method for track management
        4. Retrieve active tracks from Redis
        5. _post_process_fused_data() - Optional post-processing hook
        6. _generate_track_messages_impl() - Convert tracks to messages
        
        Returns:
            List of track messages ready for distribution to consumers
        """
        self._pre_process_raw_data()
        self._fuse_raw_observations()
        self._generate_active_tracks()
        active_tracks = self.redis_stream_helper.get_all_active_tracks_in_session(session_id=self.session_id)
        self._post_process_fused_data()
        return self._generate_track_messages_impl(active_tracks=active_tracks)

    def _generate_flight_speed_bearing(self, adjacent_points: List[LatLangAltPoint], delta_time_secs: float = 1.0) -> List[float]:
        """Calculate speed, bearing, and vertical speed between two points.
        
        Uses geodetic calculations on the WGS84 ellipsoid to compute accurate
        distance, bearing, and rates of change between consecutive observations.
        
        Args:
            adjacent_points: List containing exactly 2 LatLangAltPoint objects (first and second positions)
            delta_time_secs: Time difference in seconds between the two points (default: 1.0)
            
        Returns:
            List of [horizontal_speed_m/s, bearing_degrees, vertical_speed_m/s]
        """
        first_point = adjacent_points[0]
        second_point = adjacent_points[1]

        fwd_azimuth, _back_azimuth, adjacent_point_distance_mts = self.geod.inv(first_point.lng, first_point.lat, second_point.lng, second_point.lat)

        if fwd_azimuth < 0:
            fwd_azimuth = 360 + fwd_azimuth

        if delta_time_secs == 0:
            return [0.0, fwd_azimuth, 0.0]

        speed_mts_per_sec = adjacent_point_distance_mts / delta_time_secs
        speed_mts_per_sec = float("{:.2f}".format(speed_mts_per_sec))

        vertical_speed_mps = (second_point.alt - first_point.alt) / delta_time_secs
        vertical_speed_mps = float("{:.2f}".format(vertical_speed_mps))

        return [speed_mts_per_sec, fwd_azimuth, vertical_speed_mps]
    
    def _generate_track_messages_impl(self, active_tracks: List[ActiveTrack]) -> List[TrackMessage]:
        """Internal implementation for converting active tracks to track messages.
        
        This concrete method processes each active track to generate standardized
        ASTM F3411 compliant track messages. For each track, it:
        - Extracts the latest and previous observations
        - Calculates speed, bearing, and vertical speed using geodetic methods
        - Creates position and state objects with accuracy estimates
        - Assembles complete track messages with timestamps and identifiers
        
        Subclasses can override this method to customize track message generation,
        but the default implementation handles standard surveillance scenarios.
        
        Args:
            active_tracks: List of active tracks for the current session
            
        Returns:
            List of track messages ready for distribution to consumers
        """
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
            speed_mps, bearing_degrees, vertical_speed_mps = self._generate_flight_speed_bearing(
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

    
    def _pre_process_raw_data(self) -> bool:
        """Optional hook for pre-processing raw observations before fusion.
        
        This method is called before _fuse_raw_observations() and provides an
        extension point for subclasses to implement filtering, validation,
        or transformation of raw observations.
        
        The default implementation does nothing and returns True.
        
        Returns:
            True if pre-processing was successful, False otherwise
        """
        return True
    
    def _post_process_fused_data(self) -> bool:
        """Optional hook for post-processing after track generation.
        
        This method is called after _generate_active_tracks() but before final
        track message generation. It provides an extension point for subclasses
        to implement cleanup, validation, or additional processing.
        
        The default implementation does nothing and returns True.
        
        Returns:
            True if post-processing was successful, False otherwise
        """
        return True

    @abstractmethod
    def _fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        """Abstract method: Fuse raw observations from multiple sources.
        
        Subclasses must implement this method to define their fusion algorithm.
        This method applies data fusion algorithms to combine observations from
        multiple sensors or sources. Implementations may use techniques such as:
        - Simple deduplication (return raw observations as-is)
        - Weighted averaging
        - Kalman filtering
        - Interacting Multiple Model (IMM) estimation
        - Multi-hypothesis tracking
        
        Returns:
            List of fused air traffic observations
        """
        pass

    @abstractmethod
    def _generate_active_tracks(self) -> None:
        """Abstract method: Generate and maintain active tracks for the session.
        
        Subclasses must implement this method to define their track management
        strategy. This method creates or updates active track objects for each
        aircraft being monitored in the session. Tracks are typically stored in
        Redis for persistence across task invocations.
        
        Implementations should:
        - Group fused observations by unique aircraft identifier (e.g., ICAO address)
        - Create new tracks for newly detected aircraft
        - Update existing tracks with new observations
        - Maintain track metadata (timestamps, observation history, etc.)
        - Handle track lifecycle (initiation, maintenance, termination)
        """
        pass
