"""Convert normalized GeoJSON to stop, route and spatial indexes."""

import math

EARTH_RADIUS_M = 6371000.0

def project(lon, lat, origin_lon, origin_lat):
    origin_lat_rad = math.radians(origin_lat)

    x = EARTH_RADIUS_M * math.radians(lon - origin_lon) * math.cos(origin_lat_rad)

    y = EARTH_RADIUS_M * math.radians(lat - origin_lat)

    return x, y

def build_index(data_json, grid_size):
    features = data_json.get("features", [])

    stop_features = [
        feature
        for feature in features
        if (feature.get("properties") or {}).get("feature_type") == "stop"
    ]

    route_features = [
        feature
        for feature in features
        if (feature.get("properties") or {}).get("feature_type") == "route"
    ]

    stops = []
    stop_index = {}

    for feature in stop_features:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}

        if geometry.get("type") != "Point":
            continue

        coordinates = geometry.get("coordinates") or []

        if len(coordinates) < 2:
            continue

        stop_id = props.get("stop_id")

        if not stop_id:
            continue

        index = len(stops)
        stop_index[stop_id] = index

        stops.append(
            {
                "id": stop_id,
                "lat": coordinates[1],
                "lon": coordinates[0],
                "name": props.get("name"),
                "ref": props.get("ref"),
                "routes": props.get("routes") or [],
            }
        )

    if not stops:
        raise RuntimeError("No stop features found")

    origin_lon = sum(stop["lon"] for stop in stops) / len(stops)

    origin_lat = sum(stop["lat"] for stop in stops) / len(stops)

    grid = {}

    for index, stop in enumerate(stops):
        x, y = project(
            stop["lon"],
            stop["lat"],
            origin_lon,
            origin_lat,
        )

        stop["x"] = round(x, 1)
        stop["y"] = round(y, 1)

        gx = math.floor(x / grid_size)
        gy = math.floor(y / grid_size)
        key = f"{gx}:{gy}"

        grid.setdefault(key, []).append(index)

    routes = {}

    for feature in route_features:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry")

        if not geometry:
            continue

        route_key = props.get("route_key")

        if not route_key:
            continue

        route = routes.setdefault(
            route_key,
            {
                "route": props.get("route"),
                "mode": props.get("mode") or props.get("route"),
                "ref": props.get("ref"),
                "roundtrip": None,
                "directions": [],
            },
        )

        direction_stops = []
        direction_stop_roles = []
        stop_roles = props.get("stop_roles") or []

        for stop_pos, stop_id in enumerate(props.get("stops") or []):
            index = stop_index.get(stop_id)

            if index is not None:
                direction_stops.append(index)
                direction_stop_roles.append(
                    stop_roles[stop_pos] if stop_pos < len(stop_roles) else ""
                )

        if len(direction_stops) < 2:
            continue

        if props.get("roundtrip"):
            route["roundtrip"] = props.get("roundtrip")

        route["directions"].append(
            {
                "relation_id": props.get("relation_id"),
                "from": props.get("from"),
                "to": props.get("to"),
                "roundtrip": props.get("roundtrip"),
                "geometry_source": props.get("geometry_source", "unknown"),
                "stops": direction_stops,
                "stop_roles": direction_stop_roles,
                "geometry": geometry,
            }
        )

    routes = {
        key: route for key, route in routes.items() if route["directions"]
    }

    for route in routes.values():
        route_stops = set()

        for direction in route["directions"]:
            route_stops.update(direction["stops"])

        route["stops"] = sorted(route_stops)

    return {
        "origin": [
            round(origin_lon, 6),
            round(origin_lat, 6),
        ],
        "grid_size": grid_size,
        "grid": grid,
        "stops": stops,
        "routes": routes,
    }
