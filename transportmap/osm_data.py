"""OSM extraction and normalization. Only real route relations create ride edges.

Unverified nearby railway stations are not part of routes by default.
"""

import math
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict

import osmium

from . import PROJECT_VERSION

EARTH_RADIUS_M = 6371000.0


SOURCE_URL = "https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf"
NETWORK = (
    "\u041c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u0438\u0439 "
    "\u0442\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442"
)
SURFACE_MODES = {"bus", "tram", "trolleybus"}
RAIL_ROUTE_TYPES = {"subway", "train", "light_rail", "railway"}
SUPPORTED_ROUTE_TYPES = SURFACE_MODES | RAIL_ROUTE_TYPES
MCD_REF_PATTERN = re.compile(
    r"(?<![A-Za-z\u0410-\u042f\u0430-\u044f0-9])"
    r"[D\u0414][1-4]"
    r"(?![A-Za-z\u0410-\u042f\u0430-\u044f0-9])",
    re.IGNORECASE,
)

MCC_SHORT = "\u043c\u0446\u043a"
MCC_FULL = (
    "\u043c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u043e\u0435 "
    "\u0446\u0435\u043d\u0442\u0440\u0430\u043b\u044c\u043d\u043e"
    "\u0435 \u043a\u043e\u043b\u044c\u0446\u043e"
)
MCD_SHORT = "\u043c\u0446\u0434"
MCD_FULL = (
    "\u043c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u0438\u0435 "
    "\u0446\u0435\u043d\u0442\u0440\u0430\u043b\u044c\u043d\u044b"
    "\u0435 \u0434\u0438\u0430\u043c\u0435\u0442\u0440\u044b"
)
METROPOLITAN = (
    "\u043c\u0435\u0442\u0440\u043e\u043f\u043e\u043b\u0438\u0442"
    "\u0435\u043d"
)
MOSCOW_METROPOLITAN = (
    "\u043c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u0438\u0439 "
    "\u043c\u0435\u0442\u0440\u043e\u043f\u043e\u043b\u0438\u0442"
    "\u0435\u043d"
)


def transport_mode(tags, route_type):
    """Classify a Moscow route/route_master from OSM tags, conservatively."""
    network = (tags.get("network") or "").casefold()
    name = (tags.get("name") or "").casefold()
    ref = (tags.get("ref") or "").strip()
    text = " ".join(
        (tags.get(key) or "").casefold()
        for key in (
            "network", "network:short", "name", "name:ru", "alt_name",
            "official_name", "short_name", "description", "ref",
            "line", "brand",
        )
    )

    if route_type in SURFACE_MODES:
        return route_type if network == NETWORK.casefold() else None

    if route_type not in RAIL_ROUTE_TYPES:
        return None

    if any(
        value in text
        for value in (MCC_SHORT, MCC_FULL, "moscow central circle", "mcc")
    ) or (ref == "14" and route_type in RAIL_ROUTE_TYPES):
        return "mcc"

    if (MCD_REF_PATTERN.search(text)
            or MCD_SHORT in text
            or MCD_FULL in text
            or "moscow central diameters" in text):
        return "mcd"

    if route_type == "subway":
        # The source extract covers the Central Federal District; Moscow
        # is its only subway network. The network tag may be absent.
        return "metro"

    if route_type == "light_rail" and MOSCOW_METROPOLITAN in network:
        return "metro"

    return None


def download_source(source_path):
    source_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = source_path.with_name(source_path.stem + ".tmp" + source_path.suffix)
    tmp_path.unlink(missing_ok=True)

    for attempt in range(1, 4):
        request = urllib.request.Request(
            SOURCE_URL,
            headers={"User-Agent": f"moscow-transport-reachability/{PROJECT_VERSION}"},
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header else 0
                downloaded = 0
                next_report = 64 * 1024 * 1024

                with tmp_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)

                        if not chunk:
                            break

                        output.write(chunk)
                        downloaded += len(chunk)

                        if downloaded >= next_report:
                            if total:
                                percent = 100.0 * downloaded / total
                                print(
                                    f"Downloaded {downloaded // 1048576} MiB "
                                    f"({percent:.1f}%)"
                                )
                            else:
                                print(f"Downloaded {downloaded // 1048576} MiB")

                            next_report += 64 * 1024 * 1024

            tmp_path.replace(source_path)
            return
        except (OSError, urllib.error.URLError) as error:
            tmp_path.unlink(missing_ok=True)

            if attempt == 3:
                raise RuntimeError(f"Failed to download {SOURCE_URL}") from error

            print(f"Download failed ({attempt}/3): {error}")
            time.sleep(5)


def build_transport_extract(source_path, transport_path):
    tmp_path = transport_path.with_name(
        transport_path.stem + ".tmp" + transport_path.suffix
    )
    tmp_path.unlink(missing_ok=True)

    try:
        with osmium.BackReferenceWriter(
            str(tmp_path),
            ref_src=str(source_path),
            overwrite=True,
            remove_tags=False,
            relation_depth=1,
        ) as writer:
            discovery = TransportMasterDiscovery()
            discovery.apply_file(str(source_path))
            selector = TransportSelectionHandler(writer, discovery.child_modes)
            selector.apply_file(str(source_path))

        if selector.selected_masters == 0 and selector.selected_routes == 0:
            raise RuntimeError("No Moscow transport relations found in source PBF")

        tmp_path.replace(transport_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    print(f"selected route masters: {selector.selected_masters}")
    print(f"selected direct routes: {selector.selected_routes}")


def build_cache(cache_dir, update=False, rebuild=False):
    cache_dir.mkdir(parents=True, exist_ok=True)

    source = cache_dir / "central-fed-district.osm.pbf"
    transport_pbf = cache_dir / "moscow-transport-v4.osm.pbf"

    if update:
        print("Downloading fresh Central Federal District data...")
        download_source(source)
        transport_pbf.unlink(missing_ok=True)
    elif not source.exists():
        print("Downloading Central Federal District data...")
        download_source(source)
    else:
        print(f"Using cached: {source}")

    if rebuild:
        transport_pbf.unlink(missing_ok=True)

    if not transport_pbf.exists():
        print("Selecting Moscow surface transport, metro, MCC and MCD routes...")
        build_transport_extract(source, transport_pbf)
    else:
        print(f"Using cached: {transport_pbf}")

    return transport_pbf


def member_key(member_type, member_id):
    return f"{member_type}{member_id}"


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


def is_station(tags):
    if not tags.get("name"):
        return False
    if tags.get("railway") in ("station", "halt"):
        return True
    if tags.get("public_transport") == "station":
        return True
    return (
        tags.get("railway") == "platform"
        and tags.get("train") == "yes"
    )


class TransportMasterDiscovery(osmium.SimpleHandler):
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


class TransportSelectionHandler(osmium.SimpleHandler):
    def __init__(self, writer, child_modes):
        super().__init__()
        self.writer = writer
        self.child_modes = child_modes
        self.selected_masters = 0
        self.selected_routes = 0

    def node(self, node):
        if (is_station(node.tags) and node.location.valid()
                and station_in_area(node.location.lon, node.location.lat)):
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


class RelationHandler(osmium.SimpleHandler):
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
            # OSM PTv2 may list fewer stop positions than platforms or vice versa.
            selected = stops if len(stops) >= len(platforms) else platforms
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
                "from": relation.tags.get("from"),
                "to": relation.tags.get("to"),
                "roundtrip": relation.tags.get("roundtrip"),
                "stop_members": [member_key(item[0], item[1]) for item in selected],
                "stop_roles": [item[2] for item in selected],
                "way_members": [
                    (item[1], item[2])
                    for item in members
                    if item[0] == "w"
                    and not item[2].startswith("platform")
                    and not item[2].startswith("stop")
                ],
            }
        )


class ObjectHandler(osmium.SimpleHandler):
    def __init__(self, node_ids, stop_way_ids, route_way_ids):
        super().__init__()
        self.node_ids = node_ids
        self.stop_way_ids = stop_way_ids
        self.route_way_ids = route_way_ids
        self.objects = {}
        self.route_ways = {}
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
        if (way.id not in self.stop_way_ids and way.id not in self.route_way_ids
                and not is_station(way.tags)):
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


def coord_distance_m(a, b):
    mean_lat = math.radians((a[1] + b[1]) / 2.0)
    dx = math.radians(a[0] - b[0]) * math.cos(mean_lat)
    dy = math.radians(a[1] - b[1])
    return EARTH_RADIUS_M * math.hypot(dx, dy)


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


def enrich_rail_stops(route, mode, geometry, objects):
    """Recover station nodes missing from incomplete OSM route members."""
    if mode not in ("metro", "mcc", "mcd") or geometry is None:
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



def build_transport_data(transport_pbf, infer_rail_stops=False):
    print("Reading route relations...")
    relations = RelationHandler()
    relations.apply_file(str(transport_pbf))

    master_for_relation = {}
    master_info = {}

    for master in relations.masters:
        master_key = f"{master['route']}:master:{master['relation_id']}"
        master_info[master_key] = master

        for relation_id in master["members"]:
            master_for_relation[relation_id] = master_key

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

    for route in relations.routes:
        relation_id = route["relation_id"]
        master_key = master_for_relation.get(relation_id)
        # The route_master carries the line identity. A child route=subway
        # without MCC tags is otherwise incorrectly classified as metro.
        mode = (
            master_info[master_key]["mode"]
            if master_key is not None
            else route["mode"]
        )
        if mode is None:
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
            route_key = master_key
            ref = master.get("ref") or route.get("ref")
        else:
            ref = route.get("ref")
            route_key = (
                f"{mode}:{ref}"
                if ref
                else f"{mode}:relation:{relation_id}"
            )

        resolved_stops = []
        resolved_stop_roles = []

        for stop_id, stop_role in zip(
            route["stop_members"],
            route.get("stop_roles") or [""] * len(route["stop_members"]),
        ):
            if stop_id not in objects.objects:
                missing_stop_members += 1
                continue

            resolved_stops.append(stop_id)
            resolved_stop_roles.append(stop_role or "")
            stop_routes[stop_id].add(route_key)
            stop_relations[stop_id].add(relation_id)

        route_features.append(
            {
                "type": "Feature",
                "properties": {
                    "feature_type": "route",
                    "route_key": route_key,
                    "route": route["route"],
                    "mode": mode,
                    "ref": ref,
                    "relation_id": relation_id,
                    "from": route["from"],
                    "to": route["to"],
                    "roundtrip": route.get("roundtrip"),
                    "stops": resolved_stops,
                    "stop_roles": resolved_stop_roles,
                    "geometry_source": geometry_source,
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
    if infer_rail_stops:
        print("WARNING: heuristic railway station inference is enabled")
    mode_counts = defaultdict(int)
    for feature in route_features:
        mode_counts[feature["properties"]["mode"]] += 1
    for mode in sorted(SURFACE_MODES | {"metro", "mcc", "mcd"}):
        print(f"  {mode}: {mode_counts[mode]} route directions")
    shabolovskaya = "\u0428\u0430\u0431\u043e\u043b\u043e\u0432"
    matching = [
        feature for feature in stop_features
        if shabolovskaya.casefold()
        in (feature["properties"]["name"] or "").casefold()
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
    }
