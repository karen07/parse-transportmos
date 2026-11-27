"""Transport index for the Moscow transport map."""

import math
from collections import defaultdict

from .common import SURFACE_MODES, coord_distance_m, project
from .transport import station_route_position


def conservative_geometry_edge_lengths(
    geometry, direction_stops, stops, mode, reasons=None
):
    """Use mapped route distances only where projection is unambiguous enough.

    Stop-to-stop geometry is accepted only with close snaps, forward order,
    coherent constituent OSM ways, and plausible length versus the chord.
    Rejected edges retain the original modal circuity estimate in the UI.
    """
    count = max(0, len(direction_stops) - 1)
    rejected = [None] * count
    if not geometry or count == 0:
        if reasons is not None:
            reasons.extend(["missing_geometry"] * count)
        return rejected
    kind = geometry.get("type")
    if kind not in ("LineString", "MultiLineString"):
        if reasons is not None:
            reasons.extend(["unsupported_geometry"] * count)
        return rejected
    lines = (
        [geometry["coordinates"]] if kind == "LineString" else geometry["coordinates"]
    )
    # Build cumulative interval boundaries for discontinuous member ways.
    breaks = []
    offset = 0.0
    previous = None
    for line in lines:
        if len(line) < 2:
            continue
        if previous is not None:
            if coord_distance_m(previous, line[0]) > 80.0:
                breaks.append(offset)
        for a, b in zip(line, line[1:]):
            offset += coord_distance_m(a, b)
        previous = line[-1]
    max_snap = 100.0 if mode in SURFACE_MODES else 150.0
    located = []
    for stop_index in direction_stops:
        stop = stops[stop_index]
        located.append(station_route_position(stop["lon"], stop["lat"], geometry))
    accepted = []

    def reject(reason):
        accepted.append(None)
        if reasons is not None:
            reasons.append(reason)

    for i in range(count):
        left_snap, left_pos = located[i]
        right_snap, right_pos = located[i + 1]
        if left_snap > max_snap or right_snap > max_snap:
            reject("stop_off_geometry")
            continue
        distance = right_pos - left_pos
        if distance <= 0:
            reject("non_forward_projection")
            continue
        if any(left_pos < b < right_pos for b in breaks):
            reject("geometry_gap")
            continue
        start = stops[direction_stops[i]]
        end = stops[direction_stops[i + 1]]
        straight = coord_distance_m(
            (start["lon"], start["lat"]), (end["lon"], end["lat"])
        )
        if straight < 25.0:
            reject("close_stops")
            continue
        if distance < straight * 0.98:
            reject("shorter_than_chord")
            continue
        if distance > max(straight * 4.0, straight + 300.0):
            reject("excessive_detour")
            continue
        accepted.append(round(distance, 1))
        if reasons is not None:
            reasons.append(None)
    return accepted


def pair_surface_route_directions(features, stop_index, stops):
    """Pair orphan OSM directions by reciprocal terminals AND shared corridor.

    This identifies a *line*, not a physical vehicle or a guaranteed
    through-trip. It never pairs by ref alone, never joins railway services,
    and never joins ambiguous variants. Time-table continuity is only an
    approximation in the static reachability model.
    """
    buckets = defaultdict(list)
    for i, feat in enumerate(features):
        p = feat.get("properties") or {}
        mode = p.get("mode") or p.get("route")
        ref = (p.get("ref") or "").strip().casefold()
        network = (p.get("network") or "").strip().casefold()
        ids = [stop_index[s] for s in p.get("stops", ()) if s in stop_index]
        if p.get("independent_route") or p.get("incomplete_members"):
            continue
        if mode not in SURFACE_MODES or not ref or len(ids) < 4:
            continue
        buckets[(mode, ref, network)].append((i, ids))

    def distance(a, b):
        return coord_distance_m(
            (stops[a]["lon"], stops[a]["lat"]), (stops[b]["lon"], stops[b]["lat"])
        )

    def corridor_match(a, b):
        # Independent matched locations: opposite platforms usually lie on
        # opposite sides of one street, but two unrelated routes won't share
        # three distinct stops and both terminal locations.
        matches = []
        for stop_a in a[1:-1]:
            if any(distance(stop_a, stop_b) <= 130 for stop_b in b[1:-1]):
                if all(distance(stop_a, old) > 130 for old in matches):
                    matches.append(stop_a)
                    if len(matches) == 3:
                        return True
        return False

    candidates = defaultdict(set)
    for members in buckets.values():
        for pos, (i, a) in enumerate(members):
            for j, b in members[pos + 1 :]:
                p = features[i]["properties"]
                q = features[j]["properties"]
                if p.get("route_key") == q.get("route_key"):
                    continue  # already grouped by the OSM route_master
                if p.get("relation_id") == q.get("relation_id"):
                    continue  # clipped fragments, NOT a joined journey
                if distance(a[0], b[-1]) > 180 or distance(a[-1], b[0]) > 180:
                    continue
                if not corridor_match(a, b):
                    continue
                candidates[i].add(j)
                candidates[j].add(i)

    # Only accept unique reciprocal matches. Never choose an arbitrary
    # partner when service variants share one or both terminals.
    remap = {}
    paired = []
    for i, partners in sorted(candidates.items()):
        if len(partners) != 1:
            continue
        j = next(iter(partners))
        if j <= i or candidates[j] != {i}:
            continue
        left = features[i]["properties"]
        right = features[j]["properties"]
        key = (
            f"{left.get('mode')}:paired:"
            f"{min(str(left.get('relation_id')), str(right.get('relation_id')))}:"
            f"{max(str(left.get('relation_id')), str(right.get('relation_id')))}"
        )
        # Don't merge a multi-variant master with an orphan: this generic
        # matcher can only prove that two individual directions belong together.
        if any(
            f is not features[i]
            and f is not features[j]
            and (
                f["properties"].get("route_key")
                in (left.get("route_key"), right.get("route_key"))
            )
            for f in features
        ):
            continue
        remap[i] = key
        remap[j] = key
        paired.append((left.get("relation_id"), right.get("relation_id")))

    for i, key in remap.items():
        features[i]["properties"] = {
            **features[i]["properties"],
            "route_key": key,
            "topology_pair": True,
        }
    if paired:
        print(
            f"[routes] grouped {len(paired)} unique reciprocal pairs "
            "by terminals and corridor (not proof of same vehicle)",
            flush=True,
        )
    return paired


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

    # Group otherwise orphan surface directions BEFORE constructing routes.
    # OSM route_master grouping remains authoritative where present.
    pair_surface_route_directions(route_features, stop_index, stops)
    routes = {}
    geometry_audit = []

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
                "network": props.get("network"),
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

        if props.get("roundtrip") and not props.get("incomplete_members"):
            route["roundtrip"] = props.get("roundtrip")

        mapped_edge_meters = [None] * (len(direction_stops) - 1)
        reasons = []
        if props.get("geometry_source") == "osm_ways":
            mapped_edge_meters = conservative_geometry_edge_lengths(
                geometry, direction_stops, stops, route["mode"], reasons
            )
        else:
            reasons = ["no_mapped_geometry"] * len(mapped_edge_meters)
        for pos, reason in enumerate(reasons):
            if reason is not None:
                geometry_audit.append(
                    {
                        "route": route_key,
                        "relation_id": props.get("relation_id"),
                        "mode": route["mode"],
                        "from_id": stops[direction_stops[pos]]["id"],
                        "to_id": stops[direction_stops[pos + 1]]["id"],
                        "reason": reason,
                    }
                )
        route["directions"].append(
            {
                "relation_id": props.get("relation_id"),
                "direction_id": f"{route_key}:{props.get('relation_id')}:"
                f"{len(route['directions'])}",
                "from": props.get("from"),
                "to": props.get("to"),
                "roundtrip": (
                    None if props.get("incomplete_members") else props.get("roundtrip")
                ),
                **(
                    {"no_inferred_continuation": True}
                    if (
                        props.get("incomplete_members")
                        or props.get("independent_route")
                    )
                    else {}
                ),
                "geometry_source": props.get("geometry_source", "unknown"),
                "stops": direction_stops,
                "stop_roles": direction_stop_roles,
                "edge_meters": mapped_edge_meters,
                "geometry": geometry,
            }
        )

    routes = {key: route for key, route in routes.items() if route["directions"]}

    mapped_edges = sum(
        sum(item is not None for item in direction["edge_meters"])
        for route in routes.values()
        for direction in route["directions"]
    )
    all_edges = sum(
        len(direction["edge_meters"])
        for route in routes.values()
        for direction in route["directions"]
    )
    print(
        "[travel-model] trusted geometry edges:",
        mapped_edges,
        "/",
        all_edges,
        "(remaining edges use estimates)",
        flush=True,
    )

    for route in routes.values():
        route_stops = set()

        for direction in route["directions"]:
            route_stops.update(direction["stops"])

        route["stops"] = sorted(route_stops)

    # Any newly paired route keys must also be reflected at stops.
    for stop in stops:
        stop["routes"] = []
    for route_key, route in routes.items():
        for stop_i in route["stops"]:
            stops[stop_i]["routes"].append(route_key)
    for stop in stops:
        stop["routes"].sort()

    stop_modes = defaultdict(set)
    for route in routes.values():
        for stop_index in route["stops"]:
            stop_modes[stop_index].add(route["mode"])
    for stop_index, stop in enumerate(stops):
        stop["modes"] = sorted(stop_modes[stop_index])

    return {
        "origin": [
            round(origin_lon, 6),
            round(origin_lat, 6),
        ],
        "grid_size": grid_size,
        "grid": grid,
        "stops": stops,
        "routes": routes,
        "geometry_audit": geometry_audit,
    }
