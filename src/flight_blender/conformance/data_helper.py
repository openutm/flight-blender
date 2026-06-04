from flight_blender.scd.scd_data_definitions import Altitude, Circle, LatLngPoint, Polygon, Radius, Time, Volume3D, Volume4D


def cast_to_volume4d(volume) -> Volume4D:
    outline_polygon = None
    outline_circle = None
    if "outline_polygon" in volume["volume"].keys():
        all_vertices = volume["volume"]["outline_polygon"]["vertices"]
        polygon_verticies = []
        for vertex in all_vertices:
            v = LatLngPoint(lat=vertex["lat"], lng=vertex["lng"])
            polygon_verticies.append(v)
        polygon_verticies.pop()  # remove the last vertex to prevent interseaction

        outline_polygon = Polygon(vertices=polygon_verticies)

    if "outline_circle" in volume["volume"].keys():
        if volume["volume"]["outline_circle"]:
            circle_center = LatLngPoint(
                lat=volume["volume"]["outline_circle"]["center"]["lat"],
                lng=volume["volume"]["outline_circle"]["center"]["lng"],
            )
            circle_radius = Radius(
                value=volume["volume"]["outline_circle"]["radius"]["value"],
                units=volume["volume"]["outline_circle"]["radius"]["units"],
            )
            outline_circle = Circle(center=circle_center, radius=circle_radius)
        else:
            outline_circle = None

    altitude_lower = Altitude(
        value=volume["volume"]["altitude_lower"]["value"],
        reference=volume["volume"]["altitude_lower"]["reference"],
        units=volume["volume"]["altitude_lower"]["units"],
    )
    altitude_upper = Altitude(
        value=volume["volume"]["altitude_upper"]["value"],
        reference=volume["volume"]["altitude_upper"]["reference"],
        units=volume["volume"]["altitude_upper"]["units"],
    )
    volume_3d = Volume3D(
        outline_circle=outline_circle,
        outline_polygon=outline_polygon,
        altitude_lower=altitude_lower,
        altitude_upper=altitude_upper,
    )

    time_start = Time(format=volume["time_start"]["format"], value=volume["time_start"]["value"])
    time_end = Time(format=volume["time_end"]["format"], value=volume["time_end"]["value"])

    volume_4d = Volume4D(volume=volume_3d, time_start=time_start, time_end=time_end)

    return volume_4d
