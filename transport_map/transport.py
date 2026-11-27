"""Transport for the Moscow transport map."""

import json
import math
from collections import Counter, defaultdict

from .common import (
    EARTH_RADIUS_M,
    INTERCITY_SERVICES,
    MCC_FULL,
    MCC_SHORT,
    MCD_FULL,
    MCD_REF_PATTERN,
    MCD_SHORT,
    MOSCOW_METROPOLITAN,
    RAIL_ROUTE_TYPES,
    SUPPORTED_ROUTE_TYPES,
    SURFACE_MODES,
    OsmiumSimpleHandler,
    coord_distance_m,
    member_key,
)


def transport_mode(tags, route_type):
    """Classify public transport by OSM route type, not bus operator."""
    network = (tags.get("network") or "").casefold()
    name = (tags.get("name") or "").casefold()
    ref = (tags.get("ref") or "").strip()
    text = " ".join(
        (tags.get(key) or "").casefold()
        for key in (
            "network",
            "network:short",
            "name",
            "name:ru",
            "alt_name",
            "official_name",
            "short_name",
            "description",
            "ref",
            "line",
            "brand",
        )
    )

    if route_type in SURFACE_MODES:
        # The MKAD clip, not the operator/network tag, limits local buses.
        return route_type

    if route_type not in RAIL_ROUTE_TYPES:
        return None

    # A line number (including 14) is not enough to prove MCC identity.
    # Only railway-family relations can identify MCC/MCD services.
    rail_service = route_type in ("train", "railway", "light_rail")
    if rail_service and any(
        value in text for value in (MCC_SHORT, MCC_FULL, "moscow central circle", "mcc")
    ):
        return "mcc"

    if rail_service and (
        MCD_REF_PATTERN.search(text)
        or MCD_SHORT in text
        or MCD_FULL in text
        or "moscow central diameters" in text
    ):
        return "mcd"

    if route_type == "subway":
        # The paired Moscow/Oblast extracts cover the Moscow subway network.
        # Its route relation network tag may be absent.
        return "metro"

    if route_type == "light_rail" and MOSCOW_METROPOLITAN in network:
        return "metro"

    if route_type == "train":
        service = (tags.get("service") or "").casefold()
        train = (tags.get("train") or "").casefold()
        if service in INTERCITY_SERVICES or train in INTERCITY_SERVICES:
            return None
        return "train"

    return None


def is_night_route(route_type, ref):
    if route_type != "bus" or not ref:
        return False

    value = ref.strip().lower()

    if value.startswith("h"):
        value = "\u043d" + value[1:]

    if not value.startswith("\u043d"):
        return False

    try:
        number = int(value[1:])
    except ValueError:
        return False

    return 1 <= number <= 16


def station_in_area(lon, lat):
    return 36.3 <= lon <= 39.0 and 54.9 <= lat <= 56.4


RAIL_AUDIT_TERMS = (
    "\u0433\u0430\u0433\u0430\u0440\u0438\u043d",
    "\u043c\u043e\u0441\u043a\u0432\u043e\u0440\u0435\u0447",
)


def relevant_rail_audit_name(name):
    return bool(name) and any(term in name.casefold() for term in RAIL_AUDIT_TERMS)


def is_station(tags):
    if not tags.get("name"):
        return False
    if tags.get("railway") in ("station", "halt"):
        return True
    if tags.get("public_transport") == "station":
        return True
    return tags.get("railway") == "platform" and tags.get("train") == "yes"


class TransportMasterDiscovery(OsmiumSimpleHandler):
    def __init__(self):
        super().__init__()
        self.child_modes = {}

    def relation(self, relation):
        if relation.tags.get("type") != "route_master":
            return
        route_type = relation.tags.get("route_master")
        mode = transport_mode(relation.tags, route_type)
        if mode is None:
            return
        for member in relation.members:
            if member.type == "r":
                self.child_modes[member.ref] = mode


class TransportSelectionHandler(OsmiumSimpleHandler):
    def __init__(self, writer, child_modes):
        super().__init__()
        self.writer = writer
        self.child_modes = child_modes
        self.selected_masters = 0
        self.selected_routes = 0

    def node(self, node):
        if (
            is_station(node.tags)
            and node.location.valid()
            and station_in_area(node.location.lon, node.location.lat)
        ):
            self.writer.add_node(node)

    def way(self, way):
        if is_station(way.tags):
            self.writer.add_way(way)

    def relation(self, relation):
        relation_type = relation.tags.get("type")
        if relation_type not in ("route", "route_master"):
            return

        route_type = relation.tags.get(
            "route_master" if relation_type == "route_master" else "route"
        )
        mode = transport_mode(relation.tags, route_type)
        if relation_type == "route":
            mode = mode or self.child_modes.get(relation.id)
        if mode is None or is_night_route(route_type, relation.tags.get("ref")):
            return

        self.writer.add_relation(relation)
        if relation_type == "route_master":
            self.selected_masters += 1
        else:
            self.selected_routes += 1


class RelationHandler(OsmiumSimpleHandler):
    def __init__(self):
        super().__init__()
        self.routes = []
        self.masters = []

    def relation(self, relation):
        relation_type = relation.tags.get("type")

        if relation_type == "route_master":
            route_type = relation.tags.get("route_master")
            ref = relation.tags.get("ref")

            mode = transport_mode(relation.tags, route_type)
            if mode is None or is_night_route(route_type, ref):
                return

            members = [member.ref for member in relation.members if member.type == "r"]

            self.masters.append(
                {
                    "relation_id": relation.id,
                    "route": route_type,
                    "mode": mode,
                    "ref": ref,
                    "roundtrip": relation.tags.get("roundtrip"),
                    "network": relation.tags.get("network"),
                    "members": members,
                }
            )
            return

        if relation_type != "route":
            return

        route_type = relation.tags.get("route")
        ref = relation.tags.get("ref")

        # Child routes can inherit MCC/MCD identity from a route_master.
        # These are already limited to selected relations in transport_pbf.
        if route_type not in SUPPORTED_ROUTE_TYPES or is_night_route(route_type, ref):
            return
        mode = transport_mode(relation.tags, route_type)

        members = [
            (member.type, member.ref, member.role or "") for member in relation.members
        ]

        platforms = [
            item
            for item in members
            if item[0] in ("n", "w")
            and (item[2] == "platform" or item[2].startswith("platform_"))
        ]

        stops = [
            item
            for item in members
            if item[0] in ("n", "w")
            and (item[2] == "stop" or item[2].startswith("stop_"))
        ]

        if route_type in RAIL_ROUTE_TYPES:
            # Keep both lists for later station-by-station pairing, after the
            # member coordinates have been resolved. Never choose by counts.
            selected = [item for item in members if item in stops or item in platforms]
        elif platforms:
            selected = platforms
        elif stops:
            selected = stops
        else:
            selected = [item for item in members if item[0] == "n"]

        self.routes.append(
            {
                "relation_id": relation.id,
                "route": route_type,
                "mode": mode,
                "ref": ref,
                "network": relation.tags.get("network"),
                "from": relation.tags.get("from"),
                "to": relation.tags.get("to"),
                "roundtrip": relation.tags.get("roundtrip"),
                "stop_members": [member_key(item[0], item[1]) for item in selected],
                "stop_roles": [item[2] for item in selected],
                "rail_members": (
                    [(member_key(t, ident), role) for t, ident, role in selected]
                    if route_type in RAIL_ROUTE_TYPES
                    else []
                ),
                "way_members": [
                    (item[1], item[2])
                    for item in members
                    if item[0] == "w"
                    and not item[2].startswith("platform")
                    and not item[2].startswith("stop")
                ],
            }
        )


class ObjectHandler(OsmiumSimpleHandler):
    def __init__(self, node_ids, stop_way_ids, route_way_ids):
        super().__init__()
        self.node_ids = node_ids
        self.stop_way_ids = stop_way_ids
        self.route_way_ids = route_way_ids
        self.objects = {}
        self.route_ways = {}
        self.route_way_tags = {}
        self.stations = {}

    def node(self, node):
        if not node.location.valid():
            return
        if is_station(node.tags) and station_in_area(
            node.location.lon, node.location.lat
        ):
            key = f"n{node.id}"
            self.stations[key] = {
                "lon": node.location.lon,
                "lat": node.location.lat,
                "name": node.tags.get("name"),
                "subway": node.tags.get("station") == "subway"
                or node.tags.get("subway") == "yes",
                "mcc": transport_mode(node.tags, "train") == "mcc",
            }
        if node.id not in self.node_ids:
            return

        self.objects[f"n{node.id}"] = {
            "lon": node.location.lon,
            "lat": node.location.lat,
            "name": node.tags.get("name"),
            "ref": node.tags.get("ref"),
        }

    def way(self, way):
        if (
            way.id not in self.stop_way_ids
            and way.id not in self.route_way_ids
            and not is_station(way.tags)
        ):
            return

        coords = [
            (node.location.lon, node.location.lat)
            for node in way.nodes
            if node.location.valid()
        ]

        if not coords:
            return

        if way.id in self.route_way_ids and len(coords) >= 2:
            self.route_ways[way.id] = [
                [round(lon, 5), round(lat, 5)] for lon, lat in coords
            ]
            self.route_way_tags[way.id] = {
                "highway": way.tags.get("highway"),
                "railway": way.tags.get("railway"),
            }

        if way.id not in self.stop_way_ids and not is_station(way.tags):
            return

        min_lon = min(item[0] for item in coords)
        max_lon = max(item[0] for item in coords)
        min_lat = min(item[1] for item in coords)
        max_lat = max(item[1] for item in coords)

        lon = (min_lon + max_lon) / 2.0
        lat = (min_lat + max_lat) / 2.0
        if is_station(way.tags) and station_in_area(lon, lat):
            self.stations[f"w{way.id}"] = {
                "lon": lon,
                "lat": lat,
                "name": way.tags.get("name"),
                "subway": way.tags.get("station") == "subway"
                or way.tags.get("subway") == "yes",
                "mcc": transport_mode(way.tags, "train") == "mcc",
            }
        if way.id in self.stop_way_ids:
            self.objects[f"w{way.id}"] = {
                "lon": lon,
                "lat": lat,
                "name": way.tags.get("name"),
                "ref": way.tags.get("ref"),
            }


def explicit_way_direction(role):
    role = role.lower()

    if "backward" in role:
        return -1

    if "forward" in role:
        return 1

    return 0


def route_geometry(route, route_ways):
    lines = []

    for way_id, role in route["way_members"]:
        coords = route_ways.get(way_id)

        if coords is None or len(coords) < 2:
            continue

        line = [coord[:] for coord in coords]
        direction = explicit_way_direction(role)

        if direction < 0:
            line.reverse()

        lines.append(
            {
                "coordinates": line,
                "direction": direction,
            }
        )

    if not lines:
        return None

    for index, item in enumerate(lines):
        if item["direction"] != 0:
            continue

        line = item["coordinates"]

        if index > 0:
            previous = lines[index - 1]["coordinates"]
            to_first = coord_distance_m(previous[-1], line[0])
            to_last = coord_distance_m(previous[-1], line[-1])

            if to_last < to_first:
                line.reverse()

            continue

        if len(lines) > 1:
            next_line = lines[1]["coordinates"]
            from_first = min(
                coord_distance_m(line[0], next_line[0]),
                coord_distance_m(line[0], next_line[-1]),
            )
            from_last = min(
                coord_distance_m(line[-1], next_line[0]),
                coord_distance_m(line[-1], next_line[-1]),
            )

            if from_first < from_last:
                line.reverse()

    coordinates = [item["coordinates"] for item in lines]

    if len(coordinates) == 1:
        return {
            "type": "LineString",
            "coordinates": coordinates[0],
        }

    return {
        "type": "MultiLineString",
        "coordinates": coordinates,
    }


def station_route_position(lon, lat, geometry):
    """Return distance to route and position along its ordered geometry."""
    if geometry is None:
        return float("inf"), 0.0

    lines = (
        [geometry["coordinates"]]
        if geometry["type"] == "LineString"
        else geometry["coordinates"]
    )
    best_distance = float("inf")
    best_position = 0.0
    offset = 0.0

    for line in lines:
        for a, b in zip(line, line[1:]):
            mid_lat = math.radians((a[1] + b[1] + lat) / 3.0)
            scale_x = EARTH_RADIUS_M * math.pi / 180.0 * math.cos(mid_lat)
            scale_y = EARTH_RADIUS_M * math.pi / 180.0
            dx = (b[0] - a[0]) * scale_x
            dy = (b[1] - a[1]) * scale_y
            px = (lon - a[0]) * scale_x
            py = (lat - a[1]) * scale_y
            segment_length_sq = dx * dx + dy * dy
            if segment_length_sq == 0.0:
                continue
            fraction = max(0.0, min(1.0, (px * dx + py * dy) / segment_length_sq))
            distance = math.hypot(px - fraction * dx, py - fraction * dy)
            length = math.sqrt(segment_length_sq)
            if distance < best_distance:
                best_distance = distance
                best_position = offset + fraction * length
            offset += length

    return best_distance, best_position


def merge_rail_station_members(route, objects):
    """Pair neighboring PTv2 stop/platform members without dropping orphans.

    PTv2 normally orders stop/platform for each station. Use the platform
    coordinate as the boarding access point when a close, compatible stop
    position exists. Otherwise retain the lone member. Never manufacture
    a station or join unrelated platforms merely because they are nearby.
    """
    result = []
    merged = 0
    for key, role in route.get("rail_members", ()):
        obj = objects.objects.get(key)
        if obj is None:
            result.append((key, role))
            continue
        kind = "platform" if role.startswith("platform") else "stop"
        if result:
            prior_key, prior_role = result[-1]
            prior = objects.objects.get(prior_key)
            prior_kind = "platform" if prior_role.startswith("platform") else "stop"
            left = (prior or {}).get("name") or ""
            right = obj.get("name") or ""
            same_name = not left or not right or left.casefold() == right.casefold()
            if prior and kind != prior_kind and same_name:
                distance = coord_distance_m(
                    (prior["lon"], prior["lat"]), (obj["lon"], obj["lat"])
                )
                if distance <= 180.0:
                    # PTv2 entry/exit restrictions must survive the pairing.
                    suffix = ""
                    for tail in ("_entry_only", "_exit_only"):
                        if role.endswith(tail) or prior_role.endswith(tail):
                            suffix = tail
                            break
                    chosen_key = key if kind == "platform" else prior_key
                    chosen_kind = "platform" if kind == "platform" else prior_kind
                    chosen_role = chosen_kind + suffix
                    result[-1] = (chosen_key, chosen_role)
                    merged += 1
                    continue
        result.append((key, role))
    # An OSM relation may repeat exactly the same member; skip that only.
    unique = []
    for entry in result:
        if not unique or unique[-1] != entry:
            unique.append(entry)
    route["stop_members"] = [key for key, _role in unique]
    route["stop_roles"] = [role for _key, role in unique]
    return merged


def enrich_rail_stops(route, mode, geometry, objects):
    """Recover station nodes missing from incomplete OSM route members."""
    if mode not in ("metro", "mcc", "mcd", "train") or geometry is None:
        return

    resolved = [
        (key, role)
        for key, role in zip(route["stop_members"], route["stop_roles"])
        if key in objects.objects
    ]
    original = [(key, role, objects.objects[key]) for key, role in resolved]
    names = {
        (obj.get("name") or "").casefold().strip()
        for _key, _role, obj in original
        if obj.get("name")
    }
    inserted = []
    lines = (
        [geometry["coordinates"]]
        if geometry["type"] == "LineString"
        else geometry["coordinates"]
    )
    lon_values = [point[0] for line in lines for point in line]
    lat_values = [point[1] for line in lines for point in line]
    if not lon_values or not lat_values:
        return
    lon_min, lon_max = min(lon_values) - 0.003, max(lon_values) + 0.003
    lat_min, lat_max = min(lat_values) - 0.002, max(lat_values) + 0.002

    for key, candidate in objects.stations.items():
        if mode == "metro" and not candidate["subway"]:
            continue
        if key in route["stop_members"]:
            continue
        name = candidate["name"]
        if name.casefold().strip() in names:
            continue

        lon, lat = candidate["lon"], candidate["lat"]
        if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
            continue
        if any(
            coord_distance_m((lon, lat), (obj["lon"], obj["lat"])) < 100.0
            for _key, _role, obj in original
        ):
            continue
        if any(
            coord_distance_m((lon, lat), (obj["lon"], obj["lat"])) < 100.0
            for _key, _role, obj in inserted
        ):
            continue

        distance, position = station_route_position(lon, lat, geometry)
        if distance > 100.0:
            continue
        inserted.append((key, "stop", candidate))
        objects.objects[key] = {
            "lon": lon,
            "lat": lat,
            "name": name,
            "ref": None,
        }
        names.add(name.casefold().strip())

    if not inserted:
        return

    stops = original + inserted
    stops.sort(
        key=lambda item: station_route_position(
            item[2]["lon"], item[2]["lat"], geometry
        )[1]
    )
    route["stop_members"] = [item[0] for item in stops]
    route["stop_roles"] = [item[1] for item in stops]


def fallback_route_geometry(route, objects):
    """Use an approximate station-to-station line when OSM ways are absent."""
    coords = []
    for key in route["stop_members"]:
        obj = objects.objects.get(key)
        if obj is not None:
            point = [round(obj["lon"], 5), round(obj["lat"], 5)]
            if not coords or coords[-1] != point:
                coords.append(point)
    if len(coords) < 2:
        return None
    return {"type": "LineString", "coordinates": coords}


def route_identity(mode, ref, relation_id, master_key=None):
    """Never equate independent train directions solely by a shared ref."""
    if master_key is not None:
        return master_key
    if mode in SURFACE_MODES or mode == "train":
        return f"{mode}:relation:{relation_id}"
    return f"{mode}:{ref}" if ref else f"{mode}:relation:{relation_id}"


def resolved_route_member_runs(members, roles, available, rail=False):
    """Break route service where a missing OSM stop prevents continuity.

    In PTv2 rail relations one station is often a stop/platform pair. If
    only one member exists, its adjacent complementary member is sufficient
    to preserve that station; a missing unpaired stop is a real data gap.
    """
    members = list(members)
    roles = list(roles)
    runs = []
    current = []
    for i, stop_id in enumerate(members):
        if stop_id in available:
            current.append((stop_id, roles[i] if i < len(roles) else ""))
            continue
        kind = (
            "stop"
            if i < len(roles) and roles[i].startswith("stop")
            else (
                "platform"
                if i < len(roles) and roles[i].startswith("platform")
                else None
            )
        )
        counterpart = "platform" if kind == "stop" else "stop"
        has_pair = False
        if rail and kind is not None:
            for j in (i - 1, i + 1):
                if 0 <= j < len(members) and members[j] in available:
                    mate_role = roles[j] if j < len(roles) else ""
                    if mate_role.startswith(counterpart):
                        has_pair = True
                        break
        if has_pair:
            continue
        if len(current) >= 2:
            runs.append(current)
        current = []
    if len(current) >= 2:
        runs.append(current)
    return runs


def resolve_route_mode_and_master(route, master_key, master_info):
    """Preserve a child route's vehicle type over a conflicting route_master.

    A route_master may identify an MCD/MCC line, but must never convert
    route=bus into a railway service (or the reverse). Such OSM input
    conflicts are diagnosed instead of silently changing vehicle mode.
    """
    child_mode = route["mode"]
    if master_key is None:
        return child_mode, None, False
    master = master_info[master_key]
    child_type = route["route"]
    master_type = master["route"]
    surface_child = child_type in SURFACE_MODES
    surface_master = master_type in SURFACE_MODES
    if surface_child != surface_master or (surface_child and child_type != master_type):
        return child_mode, None, True
    if child_type in RAIL_ROUTE_TYPES and master_type in RAIL_ROUTE_TYPES:
        child_family = "train" if child_type in ("train", "railway") else child_type
        master_family = "train" if master_type in ("train", "railway") else master_type
        if child_family != master_family:
            return child_mode, None, True
        # Never use a parent to revive an excluded intercity service or
        # reclassify a subway route as suburban rail.
        if child_mode is None:
            return None, master_key, False
        if child_mode in ("mcc", "mcd"):
            return child_mode, master_key, False
        if child_mode == "train" and master_type in ("train", "railway"):
            if master["mode"] in ("mcc", "mcd"):
                return master["mode"], master_key, False
    return child_mode, master_key, False


def choose_route_master(route, parent_keys, master_info):
    """Use a unique compatible master, independent of PBF relation order."""
    compatible = []
    conflicts = []
    for key in sorted(set(parent_keys)):
        mode, master, conflict = resolve_route_mode_and_master(route, key, master_info)
        if conflict:
            conflicts.append(key)
        elif master is not None:
            compatible.append((mode, master))
    if len(compatible) == 1:
        mode, master = compatible[0]
    else:
        mode, master = route["mode"], None
    return mode, master, conflicts, [key for _, key in compatible]


MOTORIZED_ROAD_CLASSES = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "residential",
    "living_street",
    "unclassified",
    "service",
    "road",
}


RAIL_TRACK_CLASSES = {
    "rail",
    "subway",
    "light_rail",
    "narrow_gauge",
    "monorail",
    "preserved",
    "funicular",
}


def incompatible_route_way_evidence(route, way_tags):
    """Quarantine a clear road-vs-track mismatch without guessing a new mode.

    Stop-only route relations remain unchanged. A single incidental way is
    insufficient; two mapped incompatible ways and zero matching ways are
    required. This is a conservative filter, not timetable verification.
    """
    roads = rails = trams = 0
    for way_id, _role in route.get("way_members", []):
        tags = way_tags.get(way_id, {})
        roads += tags.get("highway") in MOTORIZED_ROAD_CLASSES
        rails += tags.get("railway") in RAIL_TRACK_CLASSES
        trams += tags.get("railway") == "tram"
    route_type = route["route"]
    if route_type in ("train", "railway", "subway", "light_rail"):
        if roads >= 2 and rails == 0 and trams == 0:
            return {
                "reason": "rail_service_mapped_on_roads",
                "roads": roads,
                "rail_tracks": rails,
                "tram_tracks": trams,
            }
    if route_type in ("bus", "trolleybus"):
        if rails >= 2 and roads == 0:
            return {
                "reason": "road_service_mapped_on_tracks",
                "roads": roads,
                "rail_tracks": rails,
                "tram_tracks": trams,
            }
    return None


def build_transport_data(transport_pbf, infer_rail_stops=False):
    print("Reading route relations...")
    relations = RelationHandler()
    relations.apply_file(str(transport_pbf))

    master_for_relation = defaultdict(set)
    master_info = {}

    for master in relations.masters:
        master_key = f"{master['route']}:master:{master['relation_id']}"
        master_info[master_key] = master

        for relation_id in master["members"]:
            master_for_relation[relation_id].add(master_key)

    node_ids = set()
    stop_way_ids = set()
    route_way_ids = set()

    for route in relations.routes:
        route_way_ids.update(way_id for way_id, _role in route["way_members"])

        for key in route["stop_members"]:
            if key.startswith("n"):
                node_ids.add(int(key[1:]))
            elif key.startswith("w"):
                stop_way_ids.add(int(key[1:]))

    print("Reading stops and ordered route ways...")
    objects = ObjectHandler(
        node_ids,
        stop_way_ids,
        route_way_ids,
    )
    objects.apply_file(str(transport_pbf), locations=True)

    stop_routes = defaultdict(set)
    stop_relations = defaultdict(set)
    route_features = []
    missing_stop_members = 0
    missing_stops_by_mode = Counter()
    rail_routes_with_missing_stops = []
    rail_members_merged = 0
    rail_members_orphaned = 0
    rail_routes_outside_area = 0
    mismatched_route_masters = []
    quarantined_way_conflicts = []
    ambiguous_masters = []
    missing_member_routes = []

    for route in relations.routes:
        relation_id = route["relation_id"]
        mode, master_key, conflicts, compatible = choose_route_master(
            route, master_for_relation.get(relation_id, ()), master_info
        )
        for parent_key in conflicts:
            parent = master_info[parent_key]
            mismatched_route_masters.append(
                {
                    "relation_id": relation_id,
                    "ref": route.get("ref"),
                    "child_route": route["route"],
                    "child_mode": route["mode"],
                    "master_id": parent["relation_id"],
                    "master_route": parent["route"],
                    "master_mode": parent["mode"],
                }
            )
        if len(compatible) > 1:
            ambiguous_masters.append(
                {
                    "relation_id": relation_id,
                    "ref": route.get("ref"),
                    "compatible_masters": compatible,
                }
            )
        if mode is None:
            continue
        conflict = incompatible_route_way_evidence(route, objects.route_way_tags)
        if conflict:
            quarantined_way_conflicts.append(
                {
                    "relation_id": relation_id,
                    "ref": route.get("ref"),
                    "from": route.get("from"),
                    "to": route.get("to"),
                    "route": route["route"],
                    "resolved_mode": mode,
                    **conflict,
                }
            )
            continue
        if route["route"] in RAIL_ROUTE_TYPES:
            before = len(route["rail_members"])
            rail_members_merged += merge_rail_station_members(route, objects)
            rail_members_orphaned += before - len(route["stop_members"])
        if mode == "train":
            # Test catchment inclusion without deleting intermediate stops.
            # The later MKAD clipping splits consecutive runs. Deleting stops
            # here could join railway services across a dropped section.
            inside_count = sum(
                bool(obj and station_in_area(obj["lon"], obj["lat"]))
                for key in route["stop_members"]
                for obj in [objects.objects.get(key)]
            )
            if inside_count < 2:
                rail_routes_outside_area += 1
                continue

        geometry = route_geometry(route, objects.route_ways)
        geometry_source = "osm_ways"
        if geometry is None:
            # A route relation, not proximity between unrelated stations,
            # authorizes consecutive stop-to-stop riding edges.
            geometry = fallback_route_geometry(route, objects)
            geometry_source = "stop_chain"
        if geometry is None:
            continue

        if infer_rail_stops:
            enrich_rail_stops(route, mode, geometry, objects)

        if master_key is not None:
            master = master_info[master_key]
            ref = master.get("ref") or route.get("ref")
            network = route.get("network") or master.get("network")
        else:
            ref = route.get("ref")
            network = route.get("network")
        independent = master_key is None and bool(conflicts or len(compatible) > 1)
        route_key = (
            f"{mode}:relation:{relation_id}"
            if independent
            else route_identity(mode, ref, relation_id, master_key)
        )

        direction_missing = sum(
            stop_id not in objects.objects for stop_id in route["stop_members"]
        )
        missing_stop_members += direction_missing
        missing_stops_by_mode[mode] += direction_missing
        if route["route"] in RAIL_ROUTE_TYPES:
            runs = resolved_route_member_runs(
                route["stop_members"],
                route.get("stop_roles") or [],
                objects.objects,
                rail=True,
            )
        else:
            # Surface relations select one member type per station. Missing
            # members are gaps, not permission to jump between known stops.
            runs = resolved_route_member_runs(
                route["stop_members"], route.get("stop_roles") or [], objects.objects
            )
        if direction_missing:
            missing_member_routes.append(
                {
                    "relation_id": relation_id,
                    "mode": mode,
                    "ref": ref,
                    "missing": direction_missing,
                    "continuous_runs": len(runs),
                }
            )
        if direction_missing and mode in ("mcd", "train", "mcc", "metro"):
            rail_routes_with_missing_stops.append(
                {
                    "relation_id": relation_id,
                    "mode": mode,
                    "ref": ref,
                    "from": route.get("from"),
                    "to": route.get("to"),
                    "missing": direction_missing,
                    "resolved": sum(len(run) for run in runs),
                    "continuous_runs": len(runs),
                }
            )
        for run in runs:
            resolved_stops = [key for key, _role in run]
            resolved_stop_roles = [role or "" for _key, role in run]
            run_route_key = route_key
            for stop_id in resolved_stops:
                stop_routes[stop_id].add(run_route_key)
                stop_relations[stop_id].add(relation_id)
            route_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "feature_type": "route",
                        "route_key": run_route_key,
                        "route": route["route"],
                        "mode": mode,
                        "ref": ref,
                        "network": network,
                        "relation_id": relation_id,
                        "from": route["from"],
                        "to": route["to"],
                        "roundtrip": route.get("roundtrip"),
                        "stops": resolved_stops,
                        "stop_roles": resolved_stop_roles,
                        "geometry_source": geometry_source,
                        "independent_route": independent,
                        "incomplete_members": bool(direction_missing),
                    },
                    "geometry": geometry,
                }
            )

    stop_features = []

    for stop_id in sorted(stop_routes):
        obj = objects.objects.get(stop_id)

        if obj is None:
            continue

        stop_features.append(
            {
                "type": "Feature",
                "properties": {
                    "feature_type": "stop",
                    "stop_id": stop_id,
                    "name": obj["name"],
                    "ref": obj["ref"],
                    "routes": sorted(stop_routes[stop_id]),
                    "relations": sorted(stop_relations[stop_id]),
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        round(obj["lon"], 5),
                        round(obj["lat"], 5),
                    ],
                },
            }
        )

    print(f"route relations: {len(route_features)}")
    print(f"route masters: {len(relations.masters)}")
    print(f"stops: {len(stop_features)}")
    print(f"missing stop members: {missing_stop_members}")
    print(f"paired rail stop/platform members: {rail_members_merged}")
    print(f"regional train relations outside Moscow area: {rail_routes_outside_area}")
    if infer_rail_stops:
        print("WARNING: heuristic railway station inference is enabled")
    mode_counts = defaultdict(int)
    for feature in route_features:
        mode_counts[feature["properties"]["mode"]] += 1
    for mode in sorted(SURFACE_MODES | {"metro", "mcc", "mcd", "train"}):
        print(f"  {mode}: {mode_counts[mode]} route directions")
    print(
        "[rail-input] missing OSM stop members by mode:",
        dict(missing_stops_by_mode),
        flush=True,
    )
    if mismatched_route_masters:
        print(
            "[transport] conflicting route_master vehicle types:",
            len(mismatched_route_masters),
            flush=True,
        )
        for item in mismatched_route_masters[:15]:
            print("[transport] conflict:", item, flush=True)
    if quarantined_way_conflicts:
        print(
            "[transport] quarantined road/rail way mismatches:",
            len(quarantined_way_conflicts),
            flush=True,
        )
        for item in quarantined_way_conflicts[:15]:
            print("[transport] route-way mismatch:", item, flush=True)
    shabolovskaya = "\u0428\u0430\u0431\u043e\u043b\u043e\u0432"
    matching = [
        feature
        for feature in stop_features
        if shabolovskaya.casefold() in (feature["properties"]["name"] or "").casefold()
    ]
    print(f"Shabolovskaya stop objects: {len(matching)}")
    if not matching:
        print("WARNING: Shabolovskaya is still absent from the OSM extract")
    if not mode_counts["mcc"]:
        print("WARNING: no confirmed MCC route relations in OSM extract")
        print("WARNING: no synthetic ring will be inserted into the routing graph")

    return {
        "type": "FeatureCollection",
        "features": route_features + stop_features,
        "extraction_audit": {
            "rail_stop_platform_pairs": rail_members_merged,
            "rail_member_reductions": rail_members_orphaned,
            "regional_train_relations_outside_area": rail_routes_outside_area,
            "missing_stop_members": missing_stop_members,
            "missing_stop_members_by_mode": dict(missing_stops_by_mode),
            "rail_routes_with_missing_stops": rail_routes_with_missing_stops,
            "route_master_mode_conflicts": mismatched_route_masters,
            "ambiguous_route_masters": ambiguous_masters,
            "routes_with_missing_members": missing_member_routes,
            "quarantined_route_way_conflicts": quarantined_way_conflicts,
            "named_station_candidates": [
                {
                    "id": key,
                    "name": obj["name"],
                    "lon": round(obj["lon"], 6),
                    "lat": round(obj["lat"], 6),
                }
                for key, obj in objects.stations.items()
                if relevant_rail_audit_name(obj.get("name"))
            ],
        },
    }


def normalized_route_ref(value):
    """Normalize visually equivalent Latin/Cyrillic k in route numbers."""
    return "".join(str(value or "").casefold().split()).replace("\u043a", "k")


def inspect_route_ref(feature_collection, requested_ref):
    """Print original relation IDs and decisions without rebuilding walking."""
    requested = normalized_route_ref(requested_ref)
    routes = [
        feature.get("properties", {})
        for feature in feature_collection.get("features", [])
        if feature.get("properties", {}).get("feature_type") == "route"
        and normalized_route_ref(feature["properties"].get("ref")) == requested
    ]
    audit = feature_collection.get("extraction_audit", {})
    rejected = [
        item
        for item in audit.get("quarantined_route_way_conflicts", [])
        if normalized_route_ref(item.get("ref")) == requested
    ]
    master_conflicts = [
        item
        for item in audit.get("route_master_mode_conflicts", [])
        if normalized_route_ref(item.get("ref")) == requested
    ]
    stop_names = {
        feature["properties"]["stop_id"]: feature["properties"].get("name")
        for feature in feature_collection.get("features", [])
        if feature.get("properties", {}).get("feature_type") == "stop"
    }
    print("[route-inspect] reference:", requested_ref)
    print("[route-inspect] retained directions:", len(routes))
    for route in routes:
        member_ids = route.get("stops") or []
        selected = {
            key: route.get(key)
            for key in (
                "relation_id",
                "route_key",
                "route",
                "mode",
                "ref",
                "from",
                "to",
                "geometry_source",
            )
        }
        selected["stop_count"] = len(member_ids)
        selected["first_stop"] = stop_names.get(member_ids[0]) if member_ids else None
        selected["last_stop"] = stop_names.get(member_ids[-1]) if member_ids else None
        selected["roundtrip_tag"] = route.get("roundtrip")
        print("[route-inspect] direction:", json.dumps(selected, ensure_ascii=False))
    print("[route-inspect] quarantined:", len(rejected))
    for item in rejected:
        print("[route-inspect] rejected:", json.dumps(item, ensure_ascii=True))
    print("[route-inspect] master mode conflicts:", len(master_conflicts))
    for item in master_conflicts:
        print("[route-inspect] master:", json.dumps(item, ensure_ascii=True))
    if not routes and not rejected:
        print("[route-inspect] No route found in selected OSM relations")
    return {
        "route_directions": routes,
        "quarantined": rejected,
        "master_conflicts": master_conflicts,
    }
