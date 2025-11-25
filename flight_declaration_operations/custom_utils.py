# Use this file to write your custom volume generation class and then update the environment variable to flight_declaration_operations.custom_utils.CustomVolumeGenerator


class CustomVolumeGenerator:
    def __init__(
        self,
        default_uav_speed_m_per_s,
        default_uav_climb_rate_m_per_s,
        default_uav_descent_rate_m_per_s,
    ):
        self.default_uav_speed_m_per_s = default_uav_speed_m_per_s
        self.default_uav_climb_rate_m_per_s = (default_uav_climb_rate_m_per_s,)
        self.default_uav_descent_rate_m_per_s = default_uav_descent_rate_m_per_s

    def build_v4d_from_geojson(self):
        raise NotImplementedError
