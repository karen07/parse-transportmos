"""Report transport graph provenance without silently repairing topology."""

import math
from collections import Counter

EARTH_RADIUS_M = 6371000.0


def stop_distance_m(a, b):
    lat_a = math.radians(a["lat"])
    lat_b = math.radians(b["lat"])
    dlon = math.radians(a["lon"] - b["lon"])
    dlat = lat_a - lat_b
    value = math.sin(dlat / 2) ** 2
    value += math.cos(lat_a) * math.cos(lat_b) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1, math.sqrt(value)))


def audit_index(data):
    mode_directions = Counter()
    geometry_sources = Counter()
    suspicious = []
    synthetic = []
    stops = data["stops"]

    for key, route in data["routes"].items():
        mode = route["mode"]
        for direction in route["directions"]:
            mode_directions[mode] += 1
            geometry_sources[direction["geometry_source"]] += 1
            relation_id = direction.get("relation_id")
            if not isinstance(relation_id, int) or relation_id <= 0:
                synthetic.append({"route": key, "relation_id": relation_id})
            positions = direction["stops"]
            for a, b in zip(positions, positions[1:]):
                distance = stop_distance_m(stops[a], stops[b])
                if distance > 15000:
                    suspicious.append({
                        "route": key,
                        "from": stops[a]["name"],
                        "to": stops[b]["name"],
                        "distance_m": round(distance),
                    })

    shabolovskaya = "\u0428\u0430\u0431\u043e\u043b\u043e\u0432\u0441\u043a\u0430\u044f"
    station_matches = [
        stop["name"] for stop in stops
        if stop.get("name") and shabolovskaya.casefold() in stop["name"].casefold()
    ]
    return {
        "stops": len(stops),
        "routes": len(data["routes"]),
        "mode_directions": dict(sorted(mode_directions.items())),
        "geometry_sources": dict(sorted(geometry_sources.items())),
        "synthetic_route_relations": synthetic,
        "suspicious_stop_gaps_over_15km": suspicious[:50],
        "shabolovskaya_matches": station_matches,
    }
