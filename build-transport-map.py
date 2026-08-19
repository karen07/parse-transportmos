#!/usr/bin/env python3

import argparse
import base64
import gzip
import json
import math
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import osmium

EARTH_RADIUS_M = 6371000.0
PROJECT_VERSION = "0.1.0"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the Moscow transport map directly from OpenStreetMap."
    )

    parser.add_argument(
        "-o",
        "--output",
        default="routes.html",
        help="Output HTML file (default: routes.html)",
    )

    parser.add_argument(
        "--cache-dir",
        default="cache",
        help="Cache directory for OSM PBF files (default: cache)",
    )

    parser.add_argument(
        "--update",
        action="store_true",
        help="Download a fresh Central Federal District PBF and rebuild cache",
    )

    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild the transport PBF from the cached source PBF",
    )

    parser.add_argument(
        "--walk-minutes",
        type=float,
        default=5.0,
        help="Initial walking time in minutes (default: 5)",
    )

    parser.add_argument(
        "--walk-speed",
        type=float,
        default=80.0,
        help="Walking speed in meters per minute (default: 80)",
    )

    parser.add_argument(
        "--grid-size",
        type=float,
        default=400.0,
        help="Spatial index cell size in meters (default: 400)",
    )

    parser.add_argument(
        "--circle-opacity",
        type=float,
        default=40.0,
        help="Initial reachable-circle opacity in percent (default: 40)",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {PROJECT_VERSION}",
    )

    args = parser.parse_args()

    if args.walk_minutes <= 0:
        parser.error("--walk-minutes must be greater than zero")

    if args.walk_speed <= 0:
        parser.error("--walk-speed must be greater than zero")

    if args.grid_size <= 0:
        parser.error("--grid-size must be greater than zero")

    if not 0 <= args.circle_opacity <= 100:
        parser.error("--circle-opacity must be between 0 and 100")

    if args.update and args.rebuild:
        parser.error("--update and --rebuild cannot be used together")

    return args


SOURCE_URL = "https://download.geofabrik.de/russia/central-fed-district-latest.osm.pbf"
NETWORK = (
    "\u041c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u0438\u0439 "
    "\u0442\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442"
)
ROUTE_TYPES = {"bus", "tram", "trolleybus"}


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
            selector = TransportSelectionHandler(writer)
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
    transport_pbf = cache_dir / "moscow-transport.osm.pbf"

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
        print("Selecting Moscow bus, tram and trolleybus routes...")
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


class TransportSelectionHandler(osmium.SimpleHandler):
    def __init__(self, writer):
        super().__init__()
        self.writer = writer
        self.selected_masters = 0
        self.selected_routes = 0

    def relation(self, relation):
        relation_type = relation.tags.get("type")
        network = relation.tags.get("network")

        if network != NETWORK:
            return

        if relation_type == "route_master":
            route_type = relation.tags.get("route_master")
            ref = relation.tags.get("ref")

            if route_type not in ROUTE_TYPES or is_night_route(route_type, ref):
                return

            self.writer.add_relation(relation)
            self.selected_masters += 1
            return

        if relation_type != "route":
            return

        route_type = relation.tags.get("route")
        ref = relation.tags.get("ref")

        if route_type not in ROUTE_TYPES or is_night_route(route_type, ref):
            return

        self.writer.add_relation(relation)
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

            if route_type not in ROUTE_TYPES or is_night_route(route_type, ref):
                return

            members = [member.ref for member in relation.members if member.type == "r"]

            self.masters.append(
                {
                    "relation_id": relation.id,
                    "route": route_type,
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

        if route_type not in ROUTE_TYPES or is_night_route(route_type, ref):
            return

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

        if platforms:
            selected = platforms
        elif stops:
            selected = stops
        else:
            selected = [item for item in members if item[0] == "n"]

        self.routes.append(
            {
                "relation_id": relation.id,
                "route": route_type,
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

    def node(self, node):
        if node.id not in self.node_ids or not node.location.valid():
            return

        self.objects[f"n{node.id}"] = {
            "lon": node.location.lon,
            "lat": node.location.lat,
            "name": node.tags.get("name"),
            "ref": node.tags.get("ref"),
        }

    def way(self, way):
        if way.id not in self.stop_way_ids and way.id not in self.route_way_ids:
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

        if way.id not in self.stop_way_ids:
            return

        min_lon = min(item[0] for item in coords)
        max_lon = max(item[0] for item in coords)
        min_lat = min(item[1] for item in coords)
        max_lat = max(item[1] for item in coords)

        self.objects[f"w{way.id}"] = {
            "lon": (min_lon + max_lon) / 2.0,
            "lat": (min_lat + max_lat) / 2.0,
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


def build_transport_data(transport_pbf):
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
        geometry = route_geometry(route, objects.route_ways)

        if geometry is None:
            continue

        master_key = master_for_relation.get(relation_id)

        if master_key is not None:
            master = master_info[master_key]
            route_key = master_key
            ref = master.get("ref") or route.get("ref")
        else:
            ref = route.get("ref")
            route_key = (
                f"{route['route']}:{ref}"
                if ref
                else f"{route['route']}:relation:{relation_id}"
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
                    "ref": ref,
                    "relation_id": relation_id,
                    "from": route["from"],
                    "to": route["to"],
                    "roundtrip": route.get("roundtrip"),
                    "stops": resolved_stops,
                    "stop_roles": resolved_stop_roles,
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

    return {
        "type": "FeatureCollection",
        "features": route_features + stop_features,
    }


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

        if props.get("roundtrip"):
            route["roundtrip"] = props.get("roundtrip")

        route["directions"].append(
            {
                "relation_id": props.get("relation_id"),
                "from": props.get("from"),
                "to": props.get("to"),
                "roundtrip": props.get("roundtrip"),
                "stops": direction_stops,
                "stop_roles": direction_stop_roles,
                "geometry": geometry,
            }
        )

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


def build_html(
    data,
    initial_walk_minutes,
    walk_speed,
    initial_circle_opacity,
):
    raw_payload = json.dumps(
        data,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")

    compressed_payload = gzip.compress(
        raw_payload,
        compresslevel=9,
        mtime=0,
    )

    payload = base64.b64encode(compressed_payload).decode("ascii")
    payload_lines = ",\n".join(
        f'    "{payload[offset : offset + 72]}"'
        for offset in range(0, len(payload), 72)
    )

    print(
        "embedded data:",
        f"{len(raw_payload) / (1024 * 1024):.1f} MiB raw ->",
        f"{len(compressed_payload) / (1024 * 1024):.1f} MiB gzip ->",
        f"{len(payload) / (1024 * 1024):.1f} MiB base64",
    )

    origin_lon, origin_lat = data["origin"]
    grid_size = data["grid_size"]

    initial_walk_minutes = max(
        1.0,
        min(15.0, initial_walk_minutes),
    )

    initial_circle_opacity = max(
        0.0,
        min(100.0, initial_circle_opacity),
    )

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="application-version" content="{PROJECT_VERSION}">
<title>Moscow transport reachability</title>

<link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
>

<style>
        html,
        body,
        #map {{
            width: 100%;
            height: 100%;
            margin: 0;
        }}

        html,
        body {{
            overflow: hidden;
        }}

        body {{
            font-family:
                Inter,
                ui-sans-serif,
                system-ui,
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                sans-serif;
        }}

        #map {{
            position: relative;
            z-index: 0;
        }}

        /* =========================================================
           INFO PANEL
           ========================================================= */

        #info {{
            position: absolute;

            top: 16px;
            right: 16px;

            z-index: 1000;

            box-sizing: border-box;

            width: 380px;
            max-width: calc(100vw - 32px);
            max-height: calc(100dvh - 32px);

            padding: 18px;

            overflow-y: auto;
            overscroll-behavior: contain;

            border: 1px solid rgb(226 232 240 / 0.95);
            border-radius: 20px;

            background: rgb(255 255 255 / 0.96);

            box-shadow:
                0 20px 25px -5px rgb(15 23 42 / 0.12),
                0 8px 10px -6px rgb(15 23 42 / 0.08);

            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);

            scrollbar-width: thin;

            transition:
                max-height 220ms ease,
                transform 220ms ease;
        }}

        #info::-webkit-scrollbar {{
            width: 6px;
        }}

        #info::-webkit-scrollbar-track {{
            background: transparent;
        }}

        #info::-webkit-scrollbar-thumb {{
            border-radius: 999px;
            background: rgb(148 163 184 / 0.65);
        }}

        /* =========================================================
           MOBILE SHEET TOGGLE
           ========================================================= */

        #info-collapse {{
            position: absolute;

            width: 1px;
            height: 1px;

            opacity: 0;
            pointer-events: none;
        }}

        #info-handle {{
            display: none;
        }}

        #info-content {{
            display: block;
        }}

        /* =========================================================
           PANEL HEADER
           ========================================================= */

        #info-title {{
            color: rgb(15 23 42);

            font-size: 18px;
            line-height: 1.35;
            font-weight: 700;
            letter-spacing: -0.015em;
        }}

        .info-intro {{
            padding-bottom: 14px;
            border-bottom: 1px solid rgb(241 245 249);
        }}

        .info-row {{
            margin-top: 6px;

            color: rgb(71 85 105);

            font-size: 14px;
            line-height: 1.45;
        }}

        .project-version {{
            margin-top: 7px;

            color: rgb(148 163 184);

            font-size: 10px;
            line-height: 1.2;
            font-weight: 500;
            letter-spacing: 0.02em;
        }}

        /* =========================================================
           CONTROLS
           ========================================================= */

        .control {{
            margin-top: 18px;
        }}

        .control-label {{
            display: flex;
            align-items: flex-start;
            justify-content: space-between;

            gap: 16px;

            margin-bottom: 10px;

            color: rgb(51 65 85);

            font-size: 13px;
            line-height: 1.4;
        }}

        .control-label label,
        .control-label > span {{
            font-weight: 500;
        }}

        .control-label b {{
            flex-shrink: 0;

            color: rgb(15 23 42);

            font-weight: 650;
            text-align: right;
        }}

        /* =========================================================
           RANGE
           ========================================================= */

        .control input[type="range"] {{
            display: block;

            width: 100%;
            height: 6px;

            margin: 0;

            appearance: none;
            -webkit-appearance: none;

            border-radius: 999px;

            outline: none;

            cursor: pointer;

            background: rgb(226 232 240);
        }}

        .control input[type="range"]::-webkit-slider-thumb {{
            width: 20px;
            height: 20px;

            box-sizing: border-box;

            appearance: none;
            -webkit-appearance: none;

            border: 3px solid #ffffff;
            border-radius: 50%;

            background: #2563eb;

            box-shadow:
                0 1px 3px rgb(15 23 42 / 0.25),
                0 0 0 1px rgb(37 99 235 / 0.1);

            cursor: grab;
        }}

        .control input[type="range"]::-webkit-slider-thumb:active {{
            cursor: grabbing;
        }}

        .control input[type="range"]::-moz-range-thumb {{
            width: 20px;
            height: 20px;

            box-sizing: border-box;

            border: 3px solid #ffffff;
            border-radius: 50%;

            background: #2563eb;

            box-shadow:
                0 1px 3px rgb(15 23 42 / 0.25),
                0 0 0 1px rgb(37 99 235 / 0.1);

            cursor: grab;
        }}

        .control input[type="range"]:focus-visible {{
            outline: 3px solid rgb(147 197 253 / 0.65);
            outline-offset: 5px;
        }}

        .range-scale {{
            position: relative;

            height: 10px;
            margin-top: 7px;

            color: rgb(148 163 184);

            font-size: 10px;
            line-height: 1;
        }}

        .range-scale span {{
            position: absolute;
            top: 0;

            transform: translateX(-50%);
            white-space: nowrap;
        }}

        .range-scale-walk span:nth-child(1),
        .range-scale-opacity span:nth-child(1) {{
            left: 10px;
        }}

        .range-scale-walk span:nth-child(2) {{
            left: calc(10px + (100% - 20px) * 0.285714);
        }}

        .range-scale-walk span:nth-child(3) {{
            left: calc(10px + (100% - 20px) * 0.642857);
        }}

        .range-scale-walk span:nth-child(4),
        .range-scale-opacity span:nth-child(3) {{
            left: calc(100% - 10px);
        }}

        .range-scale-opacity span:nth-child(2) {{
            left: 50%;
        }}

        /* =========================================================
           TRANSFER RANGE
           ========================================================= */

        .range-scale-transfer span:nth-child(1) {{
            left: 10px;
        }}

        .range-scale-transfer span:nth-child(2) {{
            left: 50%;
        }}

        .range-scale-transfer span:nth-child(3) {{
            left: calc(100% - 10px);
        }}

        .range-scale-time span:nth-child(1) {{
            left: 10px;
        }}

        .range-scale-time span:nth-child(2) {{
            left: calc(10px + (100% - 20px) * 0.324324);
        }}

        .range-scale-time span:nth-child(3) {{
            left: calc(10px + (100% - 20px) * 0.648649);
        }}

        .range-scale-time span:nth-child(4) {{
            left: calc(100% - 10px);
        }}

        /* =========================================================
           NOTE
           ========================================================= */

        #map.target-pick-mode {{
            cursor: crosshair;
        }}

        .note {{
            margin-top: 16px;

            padding: 10px 12px;

            border-radius: 10px;

            color: rgb(100 116 139);

            background: rgb(248 250 252);

            font-size: 11px;
            line-height: 1.45;
        }}

        /* =========================================================
           ANALYSIS
           ========================================================= */

        #analysis-summary {{
            margin-top: 18px;

            padding-top: 16px;

            border-top: 1px solid rgb(226 232 240);
        }}

        #analysis-summary .info-row {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;

            gap: 16px;

            margin-top: 8px;

            color: rgb(71 85 105);
        }}

        #analysis-summary .info-row b {{
            flex-shrink: 0;

            color: rgb(15 23 42);

            font-weight: 650;
        }}

        #routes {{
            display: flex;
            flex-wrap: wrap;
            align-items: flex-start;

            gap: 6px;

            margin-top: 14px;
        }}

        .route-group-title {{
            flex: 0 0 100%;
            box-sizing: border-box;

            margin: 10px 0 2px;
            padding: 5px 8px;

            border-radius: 7px;

            color: rgb(71 85 105);
            background: rgb(248 250 252);

            font-size: 11px;
            line-height: 1.2;
            font-weight: 700;
        }}

        .route-group-title:first-child {{
            margin-top: 0;
        }}

        .route-group-title-direct {{
            color: rgb(30 64 175);
            background: rgb(239 246 255);
            border: 1px solid rgb(147 197 253);
            font-weight: 800;
        }}

        .route-chip {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            box-sizing: border-box;

            width: 84px;
            height: 38px;
            min-height: 38px;

            margin: 0;
            padding: 0 8px;

            border-radius: 7px;

            color: #ffffff;

            font-size: 11px;
            line-height: 1;
            font-weight: 700;
            text-align: center;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;

            box-shadow:
                0 1px 2px rgb(15 23 42 / 0.15);
        }}

        /* =========================================================
           TRIP PANEL
           ========================================================= */

        #trip-panel {{
            margin-top: 18px;

            padding-top: 16px;

            border-top: 1px solid rgb(226 232 240);
        }}

        .trip-title {{
            margin-bottom: 12px;

            color: rgb(15 23 42);

            font-size: 16px;
            line-height: 1.4;
            font-weight: 700;
        }}

        /* =========================================================
           TRIP ALTERNATIVES
           New functionality from current version
           ========================================================= */

        #trip-alternatives {{
            display: flex;
            flex-direction: column;

            gap: 7px;

            margin-bottom: 14px;
        }}

        .trip-option {{
            width: 100%;

            box-sizing: border-box;

            min-height: 42px;

            padding: 9px 11px;

            border: 1px solid rgb(203 213 225);
            border-radius: 10px;

            color: rgb(51 65 85);

            background: #ffffff;

            cursor: pointer;

            text-align: left;

            font: inherit;
            font-size: 12px;
            line-height: 1.4;

            transition:
                background-color 120ms ease,
                border-color 120ms ease,
                box-shadow 120ms ease;
        }}

        .trip-option:hover {{
            border-color: rgb(148 163 184);

            background: rgb(248 250 252);
        }}

        .trip-option:focus-visible {{
            outline: 3px solid rgb(147 197 253 / 0.65);
            outline-offset: 2px;
        }}

        .trip-option-active {{
            border-color: #2563eb;

            color: rgb(30 64 175);

            background: rgb(239 246 255);

            box-shadow:
                inset 0 0 0 1px rgb(37 99 235 / 0.08);
        }}

        /* =========================================================
           TRIP STEPS
           ========================================================= */

        #trip-steps {{
            display: flex;
            flex-direction: column;

            gap: 8px;
        }}

        .trip-step {{
            margin-top: 0;

            padding: 10px 12px;

            border: 1px solid rgb(226 232 240);
            border-left-width: 4px;

            border-radius: 0 10px 10px 0;

            color: rgb(51 65 85);

            background: rgb(248 250 252);

            font-size: 13px;
            line-height: 1.45;
        }}

        .trip-step-leg1 {{
            border-left-color: #2563eb;
        }}

        .trip-step-leg2 {{
            border-left-color: #f97316;
        }}

        .trip-step-leg3 {{
            border-left-color: #7c3aed;
        }}

        .trip-step-walk {{
            border-left-color: #64748b;
        }}

        #trip-back {{
            display: inline-flex;
            align-items: center;
            justify-content: center;

            width: 100%;
            min-height: 42px;

            margin-top: 14px;
            padding: 8px 14px;

            border: 1px solid rgb(203 213 225);
            border-radius: 10px;

            color: rgb(51 65 85);

            background: #ffffff;

            font-size: 13px;
            line-height: 1;
            font-weight: 600;

            cursor: pointer;

            transition:
                background-color 120ms ease,
                border-color 120ms ease;
        }}

        #trip-back:hover {{
            border-color: rgb(148 163 184);

            background: rgb(248 250 252);
        }}

        #trip-back:active {{
            background: rgb(241 245 249);
        }}

        #trip-back:focus-visible {{
            outline: 3px solid rgb(147 197 253 / 0.65);
            outline-offset: 2px;
        }}

        /* =========================================================
           LEGEND
           ========================================================= */

        #legend {{
            position: absolute;

            left: 16px;
            bottom: 16px;

            z-index: 1000;

            box-sizing: border-box;

            min-width: 190px;

            padding: 12px 14px;

            border: 1px solid rgb(226 232 240 / 0.9);
            border-radius: 14px;

            color: rgb(71 85 105);

            background: rgb(255 255 255 / 0.94);

            box-shadow:
                0 10px 15px -3px rgb(15 23 42 / 0.1),
                0 4px 6px -4px rgb(15 23 42 / 0.08);

            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);

            font-size: 12px;
            line-height: 1.35;
        }}

        .legend-title {{
            margin-bottom: 8px;

            color: rgb(15 23 42);

            font-size: 12px;
            font-weight: 700;
        }}

        .legend-row {{
            display: flex;
            align-items: center;

            gap: 9px;

            margin-top: 6px;
        }}

        .legend-origin,
        .legend-coverage-direct,
        .legend-coverage-transfer,
        .legend-coverage-two-transfer {{
            display: inline-block;

            width: 14px;
            height: 14px;

            flex: 0 0 auto;

            border-radius: 50%;

            color: transparent;

            font-size: 0;
        }}

        .legend-origin {{
            border: 2px solid #dc2626;

            background: rgb(254 226 226 / 0.7);
        }}

        .legend-coverage-direct {{
            border: 1px solid #3b82f6;

            background: rgb(59 130 246 / 0.12);
        }}

        .legend-coverage-transfer {{
            border: 1px solid #f97316;

            background: rgb(249 115 22 / 0.12);
        }}

        .legend-coverage-two-transfer {{
            border: 1px solid #7c3aed;

            background: rgb(124 58 237 / 0.12);
        }}

        .legend-route {{
            display: inline-block;

            width: 20px;
            height: 0;

            flex: 0 0 auto;

            border-top: 4px solid #2563eb;
            border-radius: 999px;

            color: transparent;

            font-size: 0;
        }}

        /* =========================================================
           LEAFLET
           ========================================================= */

        .leaflet-control-zoom {{
            overflow: hidden;

            border: 0 !important;
            border-radius: 12px !important;

            box-shadow:
                0 4px 12px rgb(15 23 42 / 0.15) !important;
        }}

        .leaflet-control-zoom a {{
            border-color: rgb(226 232 240) !important;
        }}

        .leaflet-popup-content-wrapper {{
            border-radius: 12px;

            box-shadow:
                0 12px 24px rgb(15 23 42 / 0.16);
        }}

        /* =========================================================
           TABLET
           ========================================================= */

        @media (max-width: 900px) and (min-width: 701px) {{
            #info {{
                top: 12px;
                right: 12px;

                width: 350px;
                max-width: calc(100vw - 24px);
                max-height: calc(100dvh - 24px);

                border-radius: 16px;
            }}

            #legend {{
                left: 12px;
                bottom: 12px;
            }}
        }}

        /* =========================================================
           MOBILE
           ========================================================= */

        @media (max-width: 700px) {{
            html,
            body,
            #map {{
                height: 100dvh;
            }}

            /*
             * Bottom sheet
             */
            #info {{
                top: auto;
                right: 0;
                bottom: 0;
                left: 0;

                width: 100%;
                max-width: none;

                max-height: min(58dvh, 540px);

                padding:
                    0
                    14px
                    calc(16px + env(safe-area-inset-bottom));

                border-right: 0;
                border-bottom: 0;
                border-left: 0;

                border-radius: 18px 18px 0 0;

                box-shadow:
                    0 -6px 24px rgb(15 23 42 / 0.18);

                overscroll-behavior: contain;

                -webkit-overflow-scrolling: touch;

                transition:
                    max-height 220ms ease,
                    transform 220ms ease;
            }}

            /*
             * Always visible sheet header / drag area
             */
            #info-handle {{
                position: sticky;
                top: 0;

                z-index: 3;

                display: flex;
                align-items: center;
                justify-content: space-between;

                min-height: 54px;

                margin: 0 -14px;

                padding:
                    10px
                    14px
                    8px;

                border-radius: 18px 18px 0 0;

                background: rgb(255 255 255 / 0.98);

                cursor: pointer;

                user-select: none;

                -webkit-tap-highlight-color: transparent;
            }}

            #info-handle-left {{
                display: flex;
                flex-direction: column;

                min-width: 0;
            }}

            #info-handle-title {{
                overflow: hidden;

                color: rgb(15 23 42);

                font-size: 14px;
                line-height: 1.3;
                font-weight: 650;

                text-overflow: ellipsis;
                white-space: nowrap;
            }}

            #info-handle-hint {{
                margin-top: 2px;

                color: rgb(100 116 139);

                font-size: 10px;
                line-height: 1.2;
            }}

            #info-handle::before {{
                content: "";

                position: absolute;

                top: 6px;
                left: 50%;

                width: 36px;
                height: 4px;

                border-radius: 999px;

                background: rgb(203 213 225);

                transform: translateX(-50%);
            }}

            #info-arrow {{
                display: flex;
                align-items: center;
                justify-content: center;

                width: 30px;
                height: 30px;

                flex: 0 0 auto;

                margin-left: 12px;

                border-radius: 999px;

                color: rgb(71 85 105);

                background: rgb(241 245 249);

                transition:
                    transform 200ms ease,
                    background-color 120ms ease;
            }}

            #info-arrow svg {{
                width: 16px;
                height: 16px;
            }}

            #info-content {{
                padding-top: 5px;
            }}

            /*
             * Checked = expanded.
             *
             * Checkbox starts checked, so the sheet is open
             * when the application loads.
             */
            #info-collapse:not(:checked) ~ #info {{
                max-height:
                    calc(
                        54px +
                        env(safe-area-inset-bottom)
                    );

                overflow: hidden;
            }}

            #info-collapse:not(:checked)
            ~ #info
            #info-content {{
                visibility: hidden;

                opacity: 0;

                pointer-events: none;
            }}

            #info-collapse:checked
            ~ #info
            #info-content {{
                visibility: visible;

                opacity: 1;
            }}

            #info-collapse:not(:checked)
            ~ #info
            #info-arrow {{
                transform: rotate(180deg);
            }}

            #info-content {{
                transition:
                    opacity 120ms ease;
            }}

            .info-intro {{
                padding-top: 3px;
            }}

            #info-title {{
                font-size: 17px;
            }}

            .control {{
                margin-top: 16px;
            }}

            .control-label {{
                font-size: 14px;
            }}

            .route-chip {{
                min-height: 28px;

                padding: 4px 8px;

                font-size: 12px;
            }}

            .trip-option,
            #trip-back {{
                min-height: 44px;
            }}

            /*
             * Legend goes to the upper-right corner.
             */
            #legend {{
                top: max(8px, env(safe-area-inset-top));
                right: 8px;
                bottom: auto;
                left: auto;

                min-width: 0;
                max-width: calc(100vw - 78px);

                padding: 8px 10px;

                border-radius: 12px;

                font-size: 10px;
            }}

            .legend-title {{
                margin-bottom: 5px;

                font-size: 10px;
            }}

            .legend-row {{
                gap: 6px;

                margin-top: 4px;
            }}

            .legend-origin,
            .legend-coverage-direct,
            .legend-coverage-transfer,
            .legend-coverage-two-transfer {{
                width: 11px;
                height: 11px;
            }}

            .legend-route {{
                width: 15px;

                border-top-width: 3px;
            }}

            /*
             * Leaflet zoom buttons.
             */
            .leaflet-top.leaflet-left {{
                top: max(8px, env(safe-area-inset-top));
                left: 8px;
            }}

            .leaflet-control-zoom a {{
                width: 34px !important;
                height: 34px !important;

                line-height: 34px !important;
            }}

            /*
             * Keep bottom-right Leaflet controls
             * above the expanded sheet.
             */
            body:has(#info-collapse:checked)
            .leaflet-bottom.leaflet-right {{
                bottom: min(58dvh, 540px);
            }}

            body:has(#info-collapse:not(:checked))
            .leaflet-bottom.leaflet-right {{
                bottom: calc(
                    54px +
                    env(safe-area-inset-bottom)
                );
            }}

            .leaflet-control-attribution {{
                max-width: 55vw;

                overflow: hidden;

                white-space: nowrap;
                text-overflow: ellipsis;

                font-size: 8px;
            }}
        }}

        /* =========================================================
           MOBILE LANDSCAPE
           ========================================================= */

        @media
            (max-width: 700px)
            and (orientation: landscape) {{

            #info {{
                max-height: 72dvh;
            }}
        }}

        /* =========================================================
           VERY SMALL MOBILE
           ========================================================= */

        @media (max-width: 390px) {{
            #info {{
                max-height: 62dvh;
            }}

            .control-label {{
                gap: 10px;

                font-size: 12px;
            }}

            .note {{
                font-size: 10px;
            }}

            #analysis-summary .info-row {{
                font-size: 12px;
            }}

            #legend {{
                right: 6px;
            }}
        }}

        /* =========================================================
           REDUCED MOTION
           ========================================================= */

        @media (prefers-reduced-motion: reduce) {{
            *,
            *::before,
            *::after {{
                scroll-behavior: auto !important;

                transition-duration: 0.01ms !important;

                animation-duration: 0.01ms !important;
                animation-iteration-count: 1 !important;
            }}
        }}


.analysis-title {{
    margin-bottom: 12px;
    color: rgb(15 23 42);
    font-size: 14px;
    font-weight: 600;
}}

.analysis-value {{
    flex-shrink: 0;
}}
</style>
</head>

<body>
<input
    id="info-collapse"
    type="checkbox"
    checked
    aria-hidden="true"
>

<div id="map" aria-label="Transport reachability map"></div>

<aside id="info" aria-label="Transport reachability controls">
    <label
        id="info-handle"
        for="info-collapse"
        aria-label="Expand or collapse settings"
    >
        <span id="info-handle-left">
            <span id="info-handle-title">
                Transport reachability
            </span>
            <span id="info-handle-hint">
                Tap to expand or collapse
            </span>
        </span>
        <span id="info-arrow" aria-hidden="true">
            <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
            >
                <path d="m6 15 6-6 6 6"></path>
            </svg>
        </span>
    </label>

    <div id="info-content">
        <div class="info-intro">
            <div id="info-title">Transport reachability</div>
            <div class="info-row">
                Click once for a start point. Double-click anywhere for
                route to any point while keeping reachability mode.
            </div>
            <div class="project-version">v{PROJECT_VERSION}</div>
        </div>

        <div class="control">
            <div class="control-label">
                <label for="walk-time">Max walking time</label>
                <b id="walk-time-value"></b>
            </div>
            <input
                id="walk-time"
                type="range"
                min="1"
                max="15"
                step="1"
                value="{initial_walk_minutes:.0f}"
            >
            <div class="range-scale range-scale-walk">
                <span>1 min</span>
                <span>5</span>
                <span>10</span>
                <span>15 min</span>
            </div>
        </div>

        <div class="control">
            <div class="control-label">
                <label id="transfer-label" for="transfer-count">
                    Transfers
                </label>
                <b id="transfer-mode-value">No transfers</b>
            </div>
            <input
                id="transfer-count"
                type="range"
                min="0"
                max="2"
                step="1"
                value="0"
                aria-label="Maximum number of transfers"
            >
            <div class="range-scale range-scale-transfer">
                <span>0</span>
                <span>1</span>
                <span>2</span>
            </div>
        </div>

        <div class="control">
            <div class="control-label">
                <label for="travel-time-limit">Travel time limit</label>
                <b id="travel-time-limit-value">60 min</b>
            </div>
            <input
                id="travel-time-limit"
                type="range"
                min="0"
                max="185"
                step="5"
                value="60"
                aria-label="Maximum total travel time"
            >
            <div class="range-scale range-scale-time">
                <span>0</span>
                <span>60</span>
                <span>120</span>
                <span>No limit</span>
            </div>
        </div>

        <div class="control">
            <div class="control-label">
                <label for="circle-opacity">Circle opacity</label>
                <b id="circle-opacity-value"></b>
            </div>
            <input
                id="circle-opacity"
                type="range"
                min="0"
                max="100"
                step="1"
                value="{initial_circle_opacity:.0f}"
            >
            <div class="range-scale range-scale-opacity">
                <span>0%</span>
                <span>50%</span>
                <span>100%</span>
            </div>
        </div>

        <div class="note">
            Walking distance is approximated as a straight-line
            radius at {walk_speed:.0f} m/min.
        </div>

        <div id="analysis-summary">
            <div class="analysis-title">Reachability</div>

            <div class="info-row">
                <span>Nearby stops</span>
                <b id="nearby-count">0</b>
            </div>

            <div class="info-row">
                <span>Direct routes</span>
                <b id="direct-route-count">0</b>
            </div>

            <div class="info-row">
                <span>Without transfers</span>
                <span class="analysis-value">
                    <b id="direct-stop-count">0</b> stops
                </span>
            </div>

            <div class="info-row">
                <span>One-transfer routes</span>
                <b id="transfer-route-count">0</b>
            </div>

            <div class="info-row">
                <span>With one transfer only</span>
                <span class="analysis-value">
                    <b id="transfer-stop-count">0</b> stops
                </span>
            </div>

            <div class="info-row">
                <span>Two-transfer routes</span>
                <b id="two-transfer-route-count">0</b>
            </div>

            <div class="info-row">
                <span>With two transfers only</span>
                <span class="analysis-value">
                    <b id="two-transfer-stop-count">0</b> stops
                </span>
            </div>

            <div id="routes"></div>
        </div>

        <div id="trip-panel" hidden>
            <div id="trip-title" class="trip-title">Trip</div>
            <div id="trip-alternatives"></div>
            <div id="trip-steps"></div>
            <button id="trip-back" type="button">Back</button>
        </div>
    </div>
</aside>

<div id="legend" aria-label="Map legend">
    <div class="legend-title">Map</div>
    <div class="legend-row">
        <span class="legend-origin" aria-hidden="true">x</span>
        <span id="legend-origin-text"></span>
    </div>
    <div class="legend-row">
        <span
            class="legend-coverage-direct"
            aria-hidden="true"
        >x</span>
        <span>without transfers</span>
    </div>
    <div class="legend-row">
        <span
            class="legend-coverage-transfer"
            aria-hidden="true"
        >x</span>
        <span>with one transfer</span>
    </div>
    <div class="legend-row">
        <span
            class="legend-coverage-two-transfer"
            aria-hidden="true"
        >x</span>
        <span>with two transfers</span>
    </div>
    <div class="legend-row">
        <span class="legend-route" aria-hidden="true">x</span>
        <span>selected routes</span>
    </div>
</div>

<script>
const TRANSPORT_DATA_BASE64 = [
{payload_lines}
].join("");

function loadLeaflet() {{
    return new Promise((resolve, reject) => {{
        if (window.L) {{
            resolve();
            return;
        }}

        const script = document.createElement("script");
        script.src =
            "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
        script.onload = resolve;
        script.onerror = () => {{
            reject(new Error("Failed to load Leaflet."));
        }};
        document.head.appendChild(script);
    }});
}}

async function loadTransportData() {{
    const binary = atob(TRANSPORT_DATA_BASE64);
    const bytes = new Uint8Array(binary.length);

    for (let i = 0; i < binary.length; ++i) {{
        bytes[i] = binary.charCodeAt(i);
    }}

    if (typeof DecompressionStream !== "function") {{
        throw new Error(
            "This browser does not support DecompressionStream(gzip)."
        );
    }}

    const stream = new Blob([bytes])
        .stream()
        .pipeThrough(new DecompressionStream("gzip"));

    const text = await new Response(stream).text();
    return JSON.parse(text);
}}

(async () => {{
await loadLeaflet();
const data = await loadTransportData();

const WALK_SPEED_M_PER_MIN = {walk_speed:.6f};
const BUS_SPEED_M_PER_MIN = 320.0;
const TRAM_SPEED_M_PER_MIN = 300.0;
const TROLLEYBUS_SPEED_M_PER_MIN = 280.0;
const STOP_DELAY_MIN = 0.35;
const BOARDING_WAIT_MIN = 4.0;
const ROUGH_STOP_MIN = 1.4;
const WALK_PREFERENCE_EXTRA = 0.8;
const MAX_EVIDENCE_PER_PATH = 4;
const MAX_TRANSFER_EXTENSION_OPTIONS = 2;
const MAX_TRIP_ALTERNATIVES = 8;
const RAPTOR_ROUNDS = 3;
const MAX_RAPTOR_ONBOARD_LABELS = 4;
const MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE = 1;
const MAX_RAPTOR_TRANSFER_LABELS_PER_STOP = 8;
const REASONABLE_TIME_FACTOR = 1.5;
const REASONABLE_TIME_EXTRA_MIN = 10.0;
const GRID_SIZE = {grid_size:.6f};
const EARTH_RADIUS_M = 6371000.0;

const ORIGIN_LON = {origin_lon:.6f};
const ORIGIN_LAT = {origin_lat:.6f};
const ORIGIN_LAT_RAD = ORIGIN_LAT * Math.PI / 180.0;

const stops = data.stops;
const routes = data.routes;
const grid = data.grid;

const TEXT = {{
    stop: "Stop",
    stopGenitive: "stop",
    transfers: "Transfers",
    tripTitle: "Trip",
    backToReachability: "Back to reachability",
    noTransfers: "No transfers",
    oneTransfer: "One transfer",
    twoTransfers: "Two transfers",
    accessDirect: "Reachable: no transfers",
    accessTransfer: "Reachable: one transfer",
    accessTwoTransfers: "Reachable: two transfers",
    doubleClick: "Double-click: show trip",
    destination: "Destination",
    boarding: "Boarding",
    transferExit: "Transfer: exit",
    transferBoarding: "Transfer: boarding",
    groupDirect: "No transfers",
    groupTransfer: "After one transfer",
    groupTwoTransfers: "After two transfers",
    restoreFailedStart: "Could not restore a path to <b>",
    restoreFailedEnd: "</b> for the current walking radius and transfer mode.",
    walkPrefix: "Walk ~<b>",
    walkToStop: " min</b> to stop <b>",
    transportNotNeeded: "</b>. Transit is not needed.",
    rideStopsSuffix: " stops).",
    transferWalkPrefix: "Walk ~<b>",
    minutesColon: " min</b>: ",
    optionPrefix: "Option ",
    walkOnly: "walk",
    walkShort: "walk",
    minutesShort: "min",
    stopsShort: "stops",
}};

const walkTimeInput = document.getElementById("walk-time");
const transferCountInput = document.getElementById("transfer-count");
const travelTimeLimitInput = document.getElementById("travel-time-limit");
const circleOpacityInput = document.getElementById("circle-opacity");
const walkTimeValue = document.getElementById("walk-time-value");
const transferModeValue = document.getElementById("transfer-mode-value");
const travelTimeLimitValue = document.getElementById(
    "travel-time-limit-value"
);
const transferLabel = document.getElementById("transfer-label");
const circleOpacityValue = document.getElementById("circle-opacity-value");
const legendOriginText = document.getElementById("legend-origin-text");
const analysisSummary = document.getElementById("analysis-summary");
const tripPanel = document.getElementById("trip-panel");
const tripTitle = document.getElementById("trip-title");
const tripAlternatives = document.getElementById("trip-alternatives");
const tripSteps = document.getElementById("trip-steps");
const tripBack = document.getElementById("trip-back");

const TRAVEL_TIME_INFINITY_SLIDER_VALUE = 185;
let walkMinutes = Number(walkTimeInput.value);
let walkRadius = walkMinutes * WALK_SPEED_M_PER_MIN;
let maxTransfers = Number(transferCountInput.value);
let maxTravelMinutes = 60;
let circleOpacity = Number(circleOpacityInput.value) / 100.0;
let selectedPoint = null;
let selectedRouteFocus = null;
let renderFrame = null;
let nearbyStopCache = new Map();
let analysisCacheKey = null;
let analysisCache = null;
let targetPoint = null;
let targetPickMode = false;
let tripAlternativeIndex = 0;
const MAP_DOUBLE_CLICK_DELAY_MS = 260;
let pendingMapClickTimer = null;
let suppressMapClickUntil = 0;
let displayedStopIndexes = new Set();
let stopMarkers = new Map();
const directionShapeCache = new WeakMap();
const routeContinuationCache = new WeakMap();
const routeLegTraversalCache = new Map();
const boardingOptionsCache = new Map();
const routeEdgeMetricsCache = new Map();
const reachabilitySearchCache = new Map();
const itineraryCache = new Map();
let travelRenderTimer = null;

const map = L.map(
    "map",
    {{
        preferCanvas: true
    }}
).setView(
    [ORIGIN_LAT, ORIGIN_LON],
    10
);

map.doubleClickZoom.disable();

function createMapPane(name, zIndex, pointerEvents) {{
    const pane = map.createPane(name);
    pane.style.zIndex = String(zIndex);
    pane.style.pointerEvents = pointerEvents;
}}

createMapPane("coveragePane", 410, "none");
createMapPane("routePane", 420, "none");
createMapPane("tripWalkPane", 430, "none");
createMapPane("tripRoutePane", 440, "none");
createMapPane("stopPane", 650, "auto");
createMapPane("tripMarkerPane", 660, "auto");
createMapPane("originPane", 700, "none");

L.tileLayer(
    "https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",
    {{
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors"
    }}
).addTo(map);

const COVERAGE_SVG_NS = "http://www.w3.org/2000/svg";
const coverageSvg = document.createElementNS(COVERAGE_SVG_NS, "svg");
const coverageGroup = document.createElementNS(COVERAGE_SVG_NS, "g");
const coveragePaths = [0, 1, 2].map(() =>
    document.createElementNS(COVERAGE_SVG_NS, "path")
);

coverageSvg.style.position = "absolute";
coverageSvg.style.pointerEvents = "none";
coverageSvg.style.overflow = "visible";
coverageSvg.classList.add("leaflet-zoom-animated");
coverageSvg.appendChild(coverageGroup);

for (const transferCount of [2, 1, 0]) {{
    const path = coveragePaths[transferCount];
    path.setAttribute("stroke", "none");
    path.setAttribute("fill-rule", "nonzero");
    coverageGroup.appendChild(path);
}}

map.getPane("coveragePane").appendChild(coverageSvg);

const twoTransferRouteLayer = L.layerGroup().addTo(map);
const transferRouteLayer = L.layerGroup().addTo(map);
const directRouteLayer = L.layerGroup().addTo(map);
const stopLayer = L.layerGroup().addTo(map);
const originLayer = L.layerGroup().addTo(map);
const tripWalkLayer = L.layerGroup().addTo(map);
const tripRouteLayer = L.layerGroup().addTo(map);
const tripMarkerLayer = L.layerGroup().addTo(map);

function escapeHtml(value) {{
    if (value === null || value === undefined) {{
        return "";
    }}

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}}

function project(lon, lat) {{
    const x =
        EARTH_RADIUS_M *
        ((lon - ORIGIN_LON) * Math.PI / 180.0) *
        Math.cos(ORIGIN_LAT_RAD);

    const y =
        EARTH_RADIUS_M *
        ((lat - ORIGIN_LAT) * Math.PI / 180.0);

    return [x, y];
}}

function routeColor(key) {{
    let hash = 0;

    for (let i = 0; i < key.length; ++i) {{
        hash = ((hash << 5) - hash) + key.charCodeAt(i);
        hash |= 0;
    }}

    return "hsl(" +
        (Math.abs(hash) % 360) +
        ",70%,42%)";
}}

function updateTargetControls() {{
    map.getContainer().classList.toggle(
        "target-pick-mode",
        targetPickMode
    );
}}

function setTargetPickMode(enabled) {{
    targetPickMode = Boolean(enabled && selectedPoint);
    updateTargetControls();
}}

function updateControls() {{
    const newWalkMinutes = Number(walkTimeInput.value);
    const newWalkRadius = newWalkMinutes * WALK_SPEED_M_PER_MIN;
    const newMaxTransfers = Number(transferCountInput.value);
    const rawTravelTimeLimit = Number(travelTimeLimitInput.value);
    const newMaxTravelMinutes =
        rawTravelTimeLimit >= TRAVEL_TIME_INFINITY_SLIDER_VALUE
            ? Infinity
            : rawTravelTimeLimit;

    if (newWalkRadius !== walkRadius) {{
        nearbyStopCache.clear();
        reachabilitySearchCache.clear();
        itineraryCache.clear();
    }}

    if (
        newMaxTransfers !== maxTransfers ||
        newMaxTravelMinutes !== maxTravelMinutes
    ) {{
        itineraryCache.clear();
    }}

    walkMinutes = newWalkMinutes;
    walkRadius = newWalkRadius;
    maxTransfers = newMaxTransfers;
    maxTravelMinutes = newMaxTravelMinutes;
    circleOpacity = Number(circleOpacityInput.value) / 100.0;

    walkTimeValue.textContent =
        walkMinutes.toFixed(0) +
        " min (~" +
        Math.round(walkRadius) +
        " m)";

    transferLabel.textContent = TEXT.transfers;
    tripTitle.textContent = TEXT.tripTitle;
    tripBack.textContent = TEXT.backToReachability;

    const transferModeLabels = [
        TEXT.noTransfers,
        TEXT.oneTransfer,
        TEXT.twoTransfers
    ];
    transferModeValue.textContent = transferModeLabels[maxTransfers];

    travelTimeLimitValue.textContent = Number.isFinite(maxTravelMinutes)
        ? Math.round(maxTravelMinutes) + " min"
        : "No limit";

    circleOpacityValue.textContent =
        Math.round(circleOpacity * 100) + "%";

    legendOriginText.textContent =
        walkMinutes.toFixed(0) +
        " min (~" +
        Math.round(walkRadius) +
        " m) from selected point";

    updateTargetControls();
}}

function findNearbyStopsXY(x, y) {{
    const gx = Math.floor(x / GRID_SIZE);
    const gy = Math.floor(y / GRID_SIZE);
    const cellRadius = Math.ceil(walkRadius / GRID_SIZE);
    const radiusSquared = walkRadius * walkRadius;
    const candidates = new Set();

    for (let dx = -cellRadius; dx <= cellRadius; ++dx) {{
        for (let dy = -cellRadius; dy <= cellRadius; ++dy) {{
            const key =
                String(gx + dx) +
                ":" +
                String(gy + dy);

            const cell = grid[key];

            if (!cell) {{
                continue;
            }}

            for (const stopIndex of cell) {{
                candidates.add(stopIndex);
            }}
        }}
    }}

    const result = [];

    for (const stopIndex of candidates) {{
        const stop = stops[stopIndex];
        const dx = stop.x - x;
        const dy = stop.y - y;

        if (dx * dx + dy * dy <= radiusSquared) {{
            result.push(stopIndex);
        }}
    }}

    return result;
}}

function findNearbyStops(lat, lon) {{
    const point = project(lon, lat);
    return findNearbyStopsXY(point[0], point[1]);
}}

function findNearbyStopsFromStop(stopIndex) {{
    const cached = nearbyStopCache.get(stopIndex);

    if (cached) {{
        return cached;
    }}

    const stop = stops[stopIndex];
    const result = findNearbyStopsXY(stop.x, stop.y);

    nearbyStopCache.set(stopIndex, result);
    return result;
}}

function clearResult() {{
    pendingCoverageDisks = [[], [], []];
    activeCoverageDisks = [[], [], []];
    clearCoverageOverlay();
    twoTransferRouteLayer.clearLayers();
    transferRouteLayer.clearLayers();
    directRouteLayer.clearLayers();
    stopLayer.clearLayers();
    originLayer.clearLayers();
    tripWalkLayer.clearLayers();
    tripRouteLayer.clearLayers();
    tripMarkerLayer.clearLayers();
    displayedStopIndexes.clear();
    stopMarkers.clear();
}}

function routeLabel(route) {{
    const type = route.route || "";
    const ref = route.ref || "?";

    return type + " " + ref;
}}

function drawOrigin(lat, lon) {{
    const originWalkRadius = Number.isFinite(maxTravelMinutes)
        ? Math.min(
            walkRadius,
            Math.max(0, maxTravelMinutes) * WALK_SPEED_M_PER_MIN
        )
        : walkRadius;

    if (originWalkRadius > 0) {{
        L.circle(
            [lat, lon],
            {{
                radius: originWalkRadius,
                weight: 2,
                color: "#d32f2f",
                opacity: 0.85,
                fillColor: "#ef5350",
                fillOpacity: 0.025,
                interactive: false,
                pane: "originPane"
            }}
        ).addTo(originLayer);
    }}

    L.circleMarker(
        [lat, lon],
        {{
            radius: 7,
            weight: 2,
            color: "#111",
            fillColor: "#fff",
            fillOpacity: 1,
            interactive: false,
            pane: "originPane"
        }}
    ).addTo(originLayer);
}}

function drawRoute(routeKey, transferCount, reachableSegments) {{
    const route = routes[routeKey];

    if (!route || !reachableSegments) {{
        return;
    }}

    const color = routeColor(routeKey);
    const routeLayers = [
        directRouteLayer,
        transferRouteLayer,
        twoTransferRouteLayer
    ];
    const targetLayer = routeLayers[transferCount];
    const weights = [4, 2.8, 2.2];
    const opacities = [0.85, 0.48, 0.34];
    const accessLabels = [
        "no transfers",
        "one transfer",
        "two transfers"
    ];

    for (const segment of reachableSegments.values()) {{
        const geometry = routeSegmentGeometry(segment);

        if (!geometry) {{
            continue;
        }}

        const direction = segment.direction;
        const feature = {{
            type: "Feature",
            properties: {{}},
            geometry: geometry
        }};

        const layer = L.geoJSON(
            feature,
            {{
                pane: "routePane",
                interactive: false,
                style: {{
                    color: color,
                    weight: weights[transferCount],
                    opacity: opacities[transferCount]
                }}
            }}
        );

        const from = stops[direction.stops[segment.startPos]];
        const to = stops[direction.stops[segment.endPos]];

        layer.bindPopup(
            "<b>" +
            escapeHtml(routeLabel(route)) +
            "</b><br>" +
            escapeHtml(from ? from.name : "?") +
            " &rarr; " +
            escapeHtml(to ? to.name : "?") +
            "<br>" +
            accessLabels[transferCount]
        );

        layer.addTo(targetLayer);
    }}
}}

function coverageRadiusForArrival(arrivalMinutes) {{
    if (!Number.isFinite(maxTravelMinutes)) {{
        return walkRadius;
    }}

    if (!Number.isFinite(arrivalMinutes)) {{
        return 0;
    }}

    const remainingMinutes = Math.max(
        0,
        maxTravelMinutes - arrivalMinutes
    );

    return Math.min(
        walkRadius,
        remainingMinutes * WALK_SPEED_M_PER_MIN
    );
}}

let pendingCoverageDisks = [[], [], []];
let activeCoverageDisks = [[], [], []];

function drawCoverage(stop, transferCount, arrivalMinutes) {{
    const radius = coverageRadiusForArrival(arrivalMinutes);

    if (radius <= 0) {{
        return;
    }}

    pendingCoverageDisks[transferCount].push({{
        stop: stop,
        radius: radius
    }});
}}

function coverageCirclePath(stop, radiusMeters) {{
    const center = map.latLngToLayerPoint([stop.lat, stop.lon]);
    const latRadians = stop.lat * Math.PI / 180.0;
    const metersPerLonDegree = Math.max(
        1.0,
        111320.0 * Math.cos(latRadians)
    );
    const edge = map.latLngToLayerPoint([
        stop.lat,
        stop.lon + radiusMeters / metersPerLonDegree
    ]);
    const radiusPixels = Math.max(
        0.5,
        Math.abs(edge.x - center.x)
    );
    const x = center.x;
    const y = center.y;
    const r = radiusPixels;

    return [
        "M", x - r, y,
        "A", r, r, 0, 1, 0, x + r, y,
        "A", r, r, 0, 1, 0, x - r, y,
        "Z"
    ].join(" ");
}}

function positionCoverageOverlay() {{
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    const size = map.getSize();

    L.DomUtil.setPosition(coverageSvg, topLeft);
    coverageSvg.setAttribute("width", String(size.x));
    coverageSvg.setAttribute("height", String(size.y));
    coverageSvg.setAttribute(
        "viewBox",
        [topLeft.x, topLeft.y, size.x, size.y].join(" ")
    );
}}

function clearCoverageOverlay() {{
    for (const path of coveragePaths) {{
        path.setAttribute("d", "");
    }}
}}

function redrawCoverageOverlay() {{
    positionCoverageOverlay();

    const colors = ["#3388ff", "#ff9800", "#7c3aed"];

    /*
     * All three transfer classes are opaque INSIDE one SVG group.
     * The group opacity is then applied once.  Drawing 2 -> 1 -> 0
     * means the best (fewest-transfer) class wins in overlaps, while
     * overlapping disks within one class never become darker.
     */
    coverageGroup.setAttribute("opacity", String(circleOpacity));

    for (const transferCount of [2, 1, 0]) {{
        const path = coveragePaths[transferCount];
        const disks = activeCoverageDisks[transferCount];
        const data = disks.map(
            disk => coverageCirclePath(disk.stop, disk.radius)
        ).join(" ");

        path.setAttribute("fill", colors[transferCount]);
        path.setAttribute("fill-opacity", "1");
        path.setAttribute("d", data);
    }}
}}

function flushCoverage() {{
    activeCoverageDisks = pendingCoverageDisks.map(
        disks => disks.slice()
    );
    redrawCoverageOverlay();
}}

function stopRouteDetails(stopIndex) {{
    const stop = stops[stopIndex];
    const rows = [];
    const ordered = Array.from(stop.routes).sort(
        (a, b) => routeLabel(routes[a]).localeCompare(
            routeLabel(routes[b]),
            undefined,
            {{ numeric: true }}
        )
    );

    for (const routeKey of ordered) {{
        const route = routes[routeKey];

        if (!route) {{
            continue;
        }}

        const directions = [];

        for (const direction of route.directions) {{
            if (!direction.stops.includes(stopIndex)) {{
                continue;
            }}

            directions.push(
                escapeHtml(direction.from || "?") +
                " &rarr; " +
                escapeHtml(direction.to || "?")
            );
        }}

        rows.push(
            "<div><b>" +
            escapeHtml(routeLabel(route)) +
            "</b>" +
            (directions.length
                ? ": " + directions.join(" / ")
                : "") +
            "</div>"
        );
    }}

    return rows.join("");
}}

function stopMarkerRadiusForZoom() {{
    return 2.5;
}}

function updateStopMarkerSizes() {{
    const radius = stopMarkerRadiusForZoom();

    for (const marker of stopMarkers.values()) {{
        if (marker && typeof marker.setRadius === "function") {{
            marker.setRadius(radius);
        }}
    }}
}}

function drawStop(stopIndex, transferCount) {{
    const stop = stops[stopIndex];
    const colors = ["#1565c0", "#ef6c00", "#6d28d9"];
    const fillColors = ["#42a5f5", "#ffb74d", "#a78bfa"];
    const color = colors[transferCount];
    const fillColor = fillColors[transferCount];
    const radius = stopMarkerRadiusForZoom();
    const weight = 1;

    const marker = L.circleMarker(
        [stop.lat, stop.lon],
        {{
            radius: radius,
            weight: weight,
            color: color,
            fillColor: fillColor,
            fillOpacity: 0.9,
            bubblingMouseEvents: false,
            pane: "stopPane"
        }}
    );

    const accessLabels = [
        TEXT.accessDirect,
        TEXT.accessTransfer,
        TEXT.accessTwoTransfers
    ];
    const access = accessLabels[transferCount];

    marker.bindPopup(
        "<b>" +
        escapeHtml(stop.name || TEXT.stop) +
        "</b><br>" +
        access +
        "<br><br>" +
        stopRouteDetails(stopIndex) +
        "<br><span style=\\"color:#666\\">" +
        TEXT.doubleClick +
        "</span>"
    );

    function stopMapSelection(event) {{
        suppressMapClickUntil = performance.now() + 600;

        if (event.originalEvent) {{
            L.DomEvent.stop(event.originalEvent);
        }}
    }}

    marker.on(
        "click",
        event => {{
            stopMapSelection(event);
        }}
    );

    marker.on(
        "dblclick",
        event => {{
            stopMapSelection(event);
            marker.closePopup();
            showTripToStop(stopIndex);
        }}
    );

    marker.addTo(stopLayer);
    displayedStopIndexes.add(stopIndex);
    stopMarkers.set(stopIndex, marker);
}}

function nearestDisplayedStop(latlng, maxPixels) {{
    if (displayedStopIndexes.size === 0) {{
        return null;
    }}

    const clickPoint = map.latLngToContainerPoint(latlng);
    let bestStopIndex = null;
    let bestDistance2 = maxPixels * maxPixels;

    for (const stopIndex of displayedStopIndexes) {{
        const stop = stops[stopIndex];
        const point = map.latLngToContainerPoint(
            [stop.lat, stop.lon]
        );
        const dx = point.x - clickPoint.x;
        const dy = point.y - clickPoint.y;
        const distance2 = dx * dx + dy * dy;

        if (distance2 <= bestDistance2) {{
            bestDistance2 = distance2;
            bestStopIndex = stopIndex;
        }}
    }}

    return bestStopIndex;
}}

function openStopPopup(stopIndex) {{
    const marker = stopMarkers.get(stopIndex);

    if (marker) {{
        marker.openPopup();
    }}
}}

function stopDistanceMeters(aIndex, bIndex) {{
    const a = stops[aIndex];
    const b = stops[bIndex];
    return Math.hypot(a.x - b.x, a.y - b.y);
}}

function pointToStopDistanceMeters(point, stopIndex) {{
    const xy = project(point.lon, point.lat);
    const stop = stops[stopIndex];
    return Math.hypot(stop.x - xy[0], stop.y - xy[1]);
}}

function pointToPointDistanceMeters(a, b) {{
    const axy = project(a.lon, a.lat);
    const bxy = project(b.lon, b.lat);
    return Math.hypot(axy[0] - bxy[0], axy[1] - bxy[1]);
}}

function stopPositions(direction, stopIndex) {{
    const result = [];

    for (let pos = 0; pos < direction.stops.length; ++pos) {{
        if (direction.stops[pos] === stopIndex) {{
            result.push(pos);
        }}
    }}

    return result;
}}

function directionsBetween(routeKey, startStopIndex, endStopIndex) {{
    const route = routes[routeKey];
    const result = [];

    if (!route) {{
        return result;
    }}

    for (const direction of route.directions) {{
        const startPositions = stopPositions(
            direction,
            startStopIndex
        );

        for (const startPos of startPositions) {{
            for (
                let endPos = startPos + 1;
                endPos < direction.stops.length;
                ++endPos
            ) {{
                if (direction.stops[endPos] !== endStopIndex) {{
                    continue;
                }}

                result.push({{
                    routeKey: routeKey,
                    direction: direction,
                    startStopIndex: startStopIndex,
                    endStopIndex: endStopIndex,
                    startPos: startPos,
                    endPos: endPos,
                    rideStops: endPos - startPos
                }});
            }}
        }}
    }}

    return result;
}}

function routeSpeedMetersPerMin(routeKey) {{
    const route = routes[routeKey];
    const type = route ? route.route : "";

    if (type === "tram") {{
        return TRAM_SPEED_M_PER_MIN;
    }}

    if (type === "trolleybus") {{
        return TROLLEYBUS_SPEED_M_PER_MIN;
    }}

    return BUS_SPEED_M_PER_MIN;
}}

function geometryLengthMeters(geometry) {{
    let result = 0;

    for (const line of geometryLines(geometry)) {{
        for (let i = 0; i + 1 < line.length; ++i) {{
            result += segmentLengthMeters(line[i], line[i + 1]);
        }}
    }}

    return result;
}}

function legParts(leg) {{
    if (leg.parts && leg.parts.length) {{
        return leg.parts;
    }}

    return [{{
        direction: leg.direction,
        startPos: leg.startPos,
        endPos: leg.endPos
    }}];
}}

function legStartStopIndex(leg) {{
    const parts = legParts(leg);

    for (const part of parts) {{
        if (
            part &&
            part.direction &&
            part.direction.stops &&
            part.startPos !== undefined &&
            part.startPos >= 0 &&
            part.startPos < part.direction.stops.length
        ) {{
            return part.direction.stops[part.startPos];
        }}
    }}

    return leg.startStopIndex;
}}

function legEndStopIndex(leg) {{
    const parts = legParts(leg);

    for (let i = parts.length - 1; i >= 0; --i) {{
        const part = parts[i];

        if (
            part &&
            part.direction &&
            part.direction.stops &&
            part.endPos !== undefined &&
            part.endPos >= 0 &&
            part.endPos < part.direction.stops.length
        ) {{
            return part.direction.stops[part.endPos];
        }}
    }}

    return leg.endStopIndex;
}}

function fallbackLegDistanceMeters(leg) {{
    let result = 0;

    for (const part of legParts(leg)) {{
        for (let pos = part.startPos; pos < part.endPos; ++pos) {{
            const a = part.direction.stops[pos];
            const b = part.direction.stops[pos + 1];
            result += stopDistanceMeters(a, b);
        }}
    }}

    return result;
}}

function legDistanceMeters(leg) {{
    const geometry = routeSegmentGeometry(leg);

    if (geometry) {{
        const distance = geometryLengthMeters(geometry);

        if (distance > 0) {{
            return distance;
        }}
    }}

    return fallbackLegDistanceMeters(leg);
}}

function legEstimatedMinutes(leg) {{
    const distance = legDistanceMeters(leg);
    const speed = routeSpeedMetersPerMin(leg.routeKey);

    return {{
        distance: distance,
        minutes:
            distance / speed +
            leg.rideStops * STOP_DELAY_MIN
    }};
}}

function prepareItineraryMetrics(itinerary) {{
    if (Number.isFinite(itinerary.generalizedMinutes)) {{
        return itinerary;
    }}

    const walkTime = itinerary.walkMeters / WALK_SPEED_M_PER_MIN;
    itinerary.walkMinutes = walkTime;

    if (itinerary.type === "walk") {{
        itinerary.transitMeters = 0;
        itinerary.boardings = 0;
        itinerary.totalMinutes = walkTime;
        itinerary.generalizedMinutes =
            walkTime * (1.0 + WALK_PREFERENCE_EXTRA);
        return itinerary;
    }}

    const leg1 = legEstimatedMinutes(itinerary.leg1);
    let transitMeters = leg1.distance;
    let transitMinutes = leg1.minutes;
    let boardings = 1;

    if (itinerary.type === "transfer" || itinerary.type === "two-transfer") {{
        const leg2 = legEstimatedMinutes(itinerary.leg2);
        transitMeters += leg2.distance;
        transitMinutes += leg2.minutes;
        boardings = 2;
    }}

    if (itinerary.type === "two-transfer") {{
        const leg3 = legEstimatedMinutes(itinerary.leg3);
        transitMeters += leg3.distance;
        transitMinutes += leg3.minutes;
        boardings = 3;
    }}

    itinerary.transitMeters = transitMeters;
    itinerary.boardings = boardings;
    itinerary.totalMinutes =
        walkTime +
        transitMinutes +
        boardings * BOARDING_WAIT_MIN;
    itinerary.generalizedMinutes =
        itinerary.totalMinutes +
        walkTime * WALK_PREFERENCE_EXTRA;

    return itinerary;
}}

function withinTravelTimeLimit(itinerary) {{
    if (!Number.isFinite(maxTravelMinutes)) {{
        return true;
    }}

    prepareItineraryMetrics(itinerary);
    return itinerary.totalMinutes <= maxTravelMinutes + 1e-7;
}}

function itineraryTransferCount(itinerary) {{
    if (itinerary.type === "two-transfer") {{
        return 2;
    }}

    return itinerary.type === "transfer" ? 1 : 0;
}}

function compareItineraries(a, b) {{
    prepareItineraryMetrics(a);
    prepareItineraryMetrics(b);

    const transferDiff =
        itineraryTransferCount(a) - itineraryTransferCount(b);

    if (transferDiff !== 0) {{
        return transferDiff;
    }}

    const scoreDiff =
        a.generalizedMinutes - b.generalizedMinutes;

    if (Math.abs(scoreDiff) > 0.05) {{
        return scoreDiff;
    }}

    if (a.walkMeters !== b.walkMeters) {{
        return a.walkMeters - b.walkMeters;
    }}

    const timeDiff = a.totalMinutes - b.totalMinutes;

    if (Math.abs(timeDiff) > 0.05) {{
        return timeDiff;
    }}

    if (a.rideStops !== b.rideStops) {{
        return a.rideStops - b.rideStops;
    }}

    if (a.transitMeters !== b.transitMeters) {{
        return a.transitMeters - b.transitMeters;
    }}

    return a.initialWalk - b.initialWalk;
}}

function roughItineraryMinutes(itinerary) {{
    let boardings = 0;

    if (itinerary.type === "direct") {{
        boardings = 1;
    }} else if (itinerary.type === "transfer") {{
        boardings = 2;
    }} else if (itinerary.type === "two-transfer") {{
        boardings = 3;
    }}

    return (
        itinerary.walkMeters / WALK_SPEED_M_PER_MIN +
        itinerary.rideStops * ROUGH_STOP_MIN +
        boardings * BOARDING_WAIT_MIN
    );
}}

function roughGeneralizedMinutes(itinerary) {{
    const walkTime =
        itinerary.walkMeters / WALK_SPEED_M_PER_MIN;

    return (
        roughItineraryMinutes(itinerary) +
        walkTime * WALK_PREFERENCE_EXTRA
    );
}}

function evidenceDominates(a, b) {{
    const noMoreWalk = a.walkMeters <= b.walkMeters + 1.0;
    const noMoreStops = a.rideStops <= b.rideStops;
    const strictlyBetter =
        a.walkMeters < b.walkMeters - 1.0 ||
        a.rideStops < b.rideStops;

    return noMoreWalk && noMoreStops && strictlyBetter;
}}

function trimEvidenceBucket(bucket) {{
    if (bucket.length <= MAX_EVIDENCE_PER_PATH) {{
        return bucket;
    }}

    const keep = new Set();
    let minWalkIndex = 0;
    let minStopsIndex = 0;

    for (let i = 1; i < bucket.length; ++i) {{
        if (bucket[i].walkMeters < bucket[minWalkIndex].walkMeters) {{
            minWalkIndex = i;
        }}

        if (bucket[i].rideStops < bucket[minStopsIndex].rideStops) {{
            minStopsIndex = i;
        }}
    }}

    keep.add(bucket[minWalkIndex]);
    keep.add(bucket[minStopsIndex]);

    const ordered = bucket.slice().sort(
        (a, b) =>
            roughGeneralizedMinutes(a) -
            roughGeneralizedMinutes(b)
    );

    for (const candidate of ordered) {{
        keep.add(candidate);

        if (keep.size >= MAX_EVIDENCE_PER_PATH) {{
            break;
        }}
    }}

    return Array.from(keep);
}}

function updateEvidence(evidence, stopIndex, pathKey, candidate) {{
    if (!withinTravelTimeLimit(candidate)) {{
        return;
    }}

    let byPath = evidence.get(stopIndex);

    if (!byPath) {{
        byPath = new Map();
        evidence.set(stopIndex, byPath);
    }}

    let bucket = byPath.get(pathKey) || [];

    if (bucket.some(item => evidenceDominates(item, candidate))) {{
        return;
    }}

    bucket = bucket.filter(
        item => !evidenceDominates(candidate, item)
    );
    bucket.push(candidate);
    byPath.set(pathKey, trimEvidenceBucket(bucket));
}}

function flattenEvidence(byPath) {{
    const result = [];

    if (!byPath) {{
        return result;
    }}

    for (const bucket of byPath.values()) {{
        result.push(...bucket);
    }}

    return result;
}}

function bestTransferExtensionPaths(byPath) {{
    const bestByLastRoute = new Map();

    for (const path of flattenEvidence(byPath)) {{
        const routeKey = path.leg2.routeKey;
        const current = bestByLastRoute.get(routeKey);

        if (
            !current ||
            roughGeneralizedMinutes(path) <
                roughGeneralizedMinutes(current)
        ) {{
            bestByLastRoute.set(routeKey, path);
        }}
    }}

    return Array.from(bestByLastRoute.values())
        .sort(
            (a, b) =>
                roughGeneralizedMinutes(a) -
                roughGeneralizedMinutes(b)
        )
        .slice(0, MAX_TRANSFER_EXTENSION_OPTIONS);
}}

function addTransferBoardOption(
    boardEvidence,
    boardStopIndex,
    candidate
) {{
    if (!withinTravelTimeLimit(candidate)) {{
        return;
    }}

    let options = boardEvidence.get(boardStopIndex) || [];
    const routeKey = candidate.leg2.routeKey;
    const existingIndex = options.findIndex(
        item => item.leg2.routeKey === routeKey
    );

    if (existingIndex >= 0) {{
        if (
            roughGeneralizedMinutes(candidate) >=
            roughGeneralizedMinutes(options[existingIndex])
        ) {{
            return;
        }}

        options[existingIndex] = candidate;
    }} else {{
        options.push(candidate);
    }}

    options.sort(
        (a, b) =>
            roughGeneralizedMinutes(a) -
            roughGeneralizedMinutes(b)
    );

    if (options.length > MAX_TRANSFER_EXTENSION_OPTIONS) {{
        options = options.slice(0, MAX_TRANSFER_EXTENSION_OPTIONS);
    }}

    boardEvidence.set(boardStopIndex, options);
}}

function itineraryDominates(a, b) {{
    prepareItineraryMetrics(a);
    prepareItineraryMetrics(b);

    const noSlower = a.totalMinutes <= b.totalMinutes + 0.1;
    const noMoreWalk = a.walkMeters <= b.walkMeters + 20.0;
    const noMoreBoardings = a.boardings <= b.boardings;
    const strictlyBetter =
        a.totalMinutes < b.totalMinutes - 0.1 ||
        a.walkMeters < b.walkMeters - 20.0 ||
        a.boardings < b.boardings;

    return (
        noSlower &&
        noMoreWalk &&
        noMoreBoardings &&
        strictlyBetter
    );
}}

function itineraryIdentityKey(itinerary) {{
    if (itinerary.type === "walk") {{
        return "walk";
    }}

    const legs = candidateLegs(itinerary).map(
        leg => [
            leg.routeKey,
            String(legStartStopIndex(leg)),
            String(legEndStopIndex(leg)),
            routeLegPathKey(leg)
        ].join(":")
    );
    const transfers = [];

    if (itinerary.leg2) {{
        transfers.push([
            itinerary.transferAlightStopIndex,
            itinerary.transferBoardStopIndex
        ].join("-"));
    }}

    if (itinerary.leg3) {{
        transfers.push([
            itinerary.secondTransferAlightStopIndex,
            itinerary.secondTransferBoardStopIndex
        ].join("-"));
    }}

    return legs.join(">") + "|" + transfers.join(">");
}}

function filterReasonableItineraries(itineraries) {{
    const unique = new Map();

    for (const itinerary of itineraries) {{
        if (
            !withinTravelTimeLimit(itinerary) ||
            itineraryHasTechnicalSamePlaceLeg(itinerary)
        ) {{
            continue;
        }}

        const identity = itineraryIdentityKey(itinerary);
        const current = unique.get(identity);

        if (!current || compareItineraries(itinerary, current) < 0) {{
            unique.set(identity, itinerary);
        }}
    }}

    const sorted = Array.from(unique.values());

    for (const itinerary of sorted) {{
        prepareItineraryMetrics(itinerary);
    }}

    sorted.sort(compareItineraries);
    const pareto = [];

    for (const candidate of sorted) {{
        if (pareto.some(item => itineraryDominates(item, candidate))) {{
            continue;
        }}

        pareto.push(candidate);
    }}

    const byTransferCount = new Map();

    for (const candidate of pareto) {{
        const count = itineraryTransferCount(candidate);
        const group = byTransferCount.get(count) || [];
        group.push(candidate);
        byTransferCount.set(count, group);
    }}

    const reasonable = [];

    for (const group of byTransferCount.values()) {{
        const bestTime = Math.min(
            ...group.map(item => item.totalMinutes)
        );
        const maxTime = Math.max(
            bestTime * REASONABLE_TIME_FACTOR,
            bestTime + REASONABLE_TIME_EXTRA_MIN
        );

        reasonable.push(
            ...group.filter(
                item => item.totalMinutes <= maxTime
            )
        );
    }}

    return reasonable
        .sort(compareItineraries)
        .slice(0, MAX_TRIP_ALTERNATIVES);
}}

function relationKey(direction) {{
    if (
        direction.relation_id !== null &&
        direction.relation_id !== undefined
    ) {{
        return String(direction.relation_id);
    }}

    return [
        direction.from || "",
        direction.to || "",
        direction.stops.join(",")
    ].join(":");
}}

function findDirectItineraries(targetStopIndex, analysis) {{
    const byDirection = analysis.directEvidence.get(targetStopIndex);

    return flattenEvidence(byDirection).sort(compareItineraries);
}}

function findTransferItineraries(targetStopIndex, analysis) {{
    if (maxTransfers < 1) {{
        return [];
    }}

    const byPair = analysis.transferEvidence.get(targetStopIndex);

    return flattenEvidence(byPair).sort(compareItineraries);
}}

function findTwoTransferItineraries(targetStopIndex, analysis) {{
    if (maxTransfers < 2) {{
        return [];
    }}

    const byTriple = analysis.twoTransferEvidence.get(targetStopIndex);

    return flattenEvidence(byTriple).sort(compareItineraries);
}}

function findItineraries(targetStopIndex) {{
    const key = selectionKey() + ":" + String(targetStopIndex);
    const cached = itineraryCache.get(key);

    if (cached) {{
        return cached;
    }}

    const result = [];
    const targetWalk = pointToStopDistanceMeters(
        selectedPoint,
        targetStopIndex
    );

    if (targetWalk <= walkRadius) {{
        result.push({{
            type: "walk",
            walkMeters: targetWalk,
            rideStops: 0,
            initialWalk: targetWalk
        }});
    }}

    const analysis = analyzeSelection();

    result.push(
        ...findDirectItineraries(
            targetStopIndex,
            analysis
        )
    );

    if (maxTransfers >= 1) {{
        result.push(
            ...findTransferItineraries(
                targetStopIndex,
                analysis
            )
        );
    }}

    if (maxTransfers >= 2) {{
        result.push(
            ...findTwoTransferItineraries(
                targetStopIndex,
                analysis
            )
        );
    }}

    const filtered = filterReasonableItineraries(result);
    itineraryCache.set(key, filtered);
    return filtered;
}}

function copyItineraryForTargetPoint(
    itinerary,
    targetStopIndex,
    finalWalk
) {{
    prepareItineraryMetrics(itinerary);
    const finalWalkMinutes = finalWalk / WALK_SPEED_M_PER_MIN;

    return {{
        ...itinerary,
        targetStopIndex: targetStopIndex,
        finalWalk: finalWalk,
        walkMeters: itinerary.walkMeters + finalWalk,
        walkMinutes: itinerary.walkMinutes + finalWalkMinutes,
        totalMinutes: itinerary.totalMinutes + finalWalkMinutes,
        generalizedMinutes:
            itinerary.generalizedMinutes +
            finalWalkMinutes * (1.0 + WALK_PREFERENCE_EXTRA)
    }};
}}

function targetPointKey(point) {{
    return [
        point.lat.toFixed(6),
        point.lon.toFixed(6)
    ].join(":");
}}

function findPointItineraries(point) {{
    const key = selectionKey() + ":point:" + targetPointKey(point);
    const cached = itineraryCache.get(key);

    if (cached) {{
        return cached;
    }}

    const result = [];
    const directWalk = pointToPointDistanceMeters(
        selectedPoint,
        point
    );

    if (directWalk <= walkRadius) {{
        result.push({{
            type: "walk",
            walkMeters: directWalk,
            rideStops: 0,
            initialWalk: directWalk,
            finalWalk: directWalk,
            targetStopIndex: null
        }});
    }}

    const analysis = analyzeSelection();
    const targetStops = findNearbyStops(point.lat, point.lon);

    for (const stopIndex of targetStops) {{
        const finalWalk = pointToStopDistanceMeters(
            point,
            stopIndex
        );

        const direct = findDirectItineraries(
            stopIndex,
            analysis
        );

        for (const itinerary of direct) {{
            result.push(
                copyItineraryForTargetPoint(
                    itinerary,
                    stopIndex,
                    finalWalk
                )
            );
        }}

        if (maxTransfers >= 1) {{
            const transfer = findTransferItineraries(
                stopIndex,
                analysis
            );

            for (const itinerary of transfer) {{
                result.push(
                    copyItineraryForTargetPoint(
                        itinerary,
                        stopIndex,
                        finalWalk
                    )
                );
            }}
        }}

        if (maxTransfers >= 2) {{
            const twoTransfer = findTwoTransferItineraries(
                stopIndex,
                analysis
            );

            for (const itinerary of twoTransfer) {{
                result.push(
                    copyItineraryForTargetPoint(
                        itinerary,
                        stopIndex,
                        finalWalk
                    )
                );
            }}
        }}
    }}

    const filtered = filterReasonableItineraries(result);
    itineraryCache.set(key, filtered);
    return filtered;
}}

function geometryLines(geometry) {{
    if (!geometry) {{
        return [];
    }}

    if (geometry.type === "LineString") {{
        return geometry.coordinates ? [geometry.coordinates] : [];
    }}

    if (geometry.type === "MultiLineString") {{
        return geometry.coordinates || [];
    }}

    if (geometry.type === "GeometryCollection") {{
        const result = [];

        for (const item of geometry.geometries || []) {{
            result.push(...geometryLines(item));
        }}

        return result;
    }}

    return [];
}}

function segmentLengthMeters(a, b) {{
    const ax = project(a[0], a[1]);
    const bx = project(b[0], b[1]);
    return Math.hypot(ax[0] - bx[0], ax[1] - bx[1]);
}}

function orderedDirectionParts(direction) {{
    const sourceLines = geometryLines(direction.geometry)
        .filter(line => line && line.length >= 2)
        .map(
            line => line.map(
                coord => [coord[0], coord[1]]
            )
        );
    const parts = [];
    let current = null;

    for (const line of sourceLines) {{
        if (!current) {{
            current = line.slice();
            continue;
        }}

        const currentEnd = current[current.length - 1];
        const gap = segmentLengthMeters(
            currentEnd,
            line[0]
        );

        if (gap <= 3.0) {{
            if (gap <= 0.5) {{
                current.push(...line.slice(1));
            }} else {{
                current.push(...line);
            }}
        }} else {{
            parts.push(current);
            current = line.slice();
        }}
    }}

    if (current) {{
        parts.push(current);
    }}

    return parts;
}}

function projectStopToSegment(stop, segment) {{
    const a = segment.aXY;
    const b = segment.bXY;
    const vx = b[0] - a[0];
    const vy = b[1] - a[1];
    const length2 = vx * vx + vy * vy;
    let t = 0;

    if (length2 > 0) {{
        t = (
            (stop.x - a[0]) * vx +
            (stop.y - a[1]) * vy
        ) / length2;
        t = Math.max(0, Math.min(1, t));
    }}

    const x = a[0] + vx * t;
    const y = a[1] + vy * t;
    const distance = Math.hypot(stop.x - x, stop.y - y);
    const lon = segment.a[0] + (segment.b[0] - segment.a[0]) * t;
    const lat = segment.a[1] + (segment.b[1] - segment.a[1]) * t;

    return {{
        distance: distance,
        progress: segment.order + t,
        partIndex: segment.partIndex,
        segmentIndex: segment.segmentIndex,
        t: t,
        coord: [lon, lat]
    }};
}}

function chooseStopAnchor(segments, stopIndex, minProgress) {{
    const stop = stops[stopIndex];
    let best = null;

    for (const segment of segments) {{
        if (segment.order + 1 < minProgress) {{
            continue;
        }}

        const candidate = projectStopToSegment(stop, segment);

        if (candidate.progress + 1e-7 < minProgress) {{
            continue;
        }}

        if (!best || candidate.distance < best.distance) {{
            best = candidate;
            continue;
        }}

        if (
            Math.abs(candidate.distance - best.distance) <= 5.0 &&
            candidate.progress < best.progress
        ) {{
            best = candidate;
        }}
    }}

    return best;
}}

function getDirectionShape(direction) {{
    const cached = directionShapeCache.get(direction);

    if (cached) {{
        return cached;
    }}

    const parts = orderedDirectionParts(direction);
    const segments = [];
    let order = 0;

    parts.forEach(
        (part, partIndex) => {{
            for (let i = 0; i + 1 < part.length; ++i) {{
                segments.push({{
                    order: order,
                    partIndex: partIndex,
                    segmentIndex: i,
                    a: part[i],
                    b: part[i + 1],
                    aXY: project(part[i][0], part[i][1]),
                    bXY: project(part[i + 1][0], part[i + 1][1])
                }});
                ++order;
            }}
        }}
    );

    const anchors = [];
    let minProgress = 0;

    for (const stopIndex of direction.stops) {{
        const anchor = chooseStopAnchor(
            segments,
            stopIndex,
            minProgress
        );

        anchors.push(anchor);

        if (anchor) {{
            minProgress = anchor.progress + 1e-6;
        }}
    }}

    const shape = {{
        parts: parts,
        anchors: anchors
    }};

    directionShapeCache.set(direction, shape);
    return shape;
}}

function pushUniqueCoord(target, coord) {{
    if (target.length === 0) {{
        target.push(coord);
        return;
    }}

    const last = target[target.length - 1];

    if (last[0] === coord[0] && last[1] === coord[1]) {{
        return;
    }}

    target.push(coord);
}}

function samePartSegment(part, start, end) {{
    const result = [];

    pushUniqueCoord(result, start.coord);

    if (start.segmentIndex === end.segmentIndex) {{
        pushUniqueCoord(result, end.coord);
        return result;
    }}

    for (
        let i = start.segmentIndex + 1;
        i <= end.segmentIndex;
        ++i
    ) {{
        pushUniqueCoord(result, part[i]);
    }}

    pushUniqueCoord(result, end.coord);
    return result;
}}

function firstPartSegment(part, start) {{
    const result = [];

    pushUniqueCoord(result, start.coord);

    for (let i = start.segmentIndex + 1; i < part.length; ++i) {{
        pushUniqueCoord(result, part[i]);
    }}

    return result;
}}

function lastPartSegment(part, end) {{
    const result = [];

    for (let i = 0; i <= end.segmentIndex; ++i) {{
        pushUniqueCoord(result, part[i]);
    }}

    pushUniqueCoord(result, end.coord);
    return result;
}}

function routeSinglePartGeometry(part) {{
    const direction = part.direction;
    const startPos = part.startPos;
    const endPos = part.endPos;

    if (
        startPos === undefined ||
        endPos === undefined ||
        startPos < 0 ||
        endPos <= startPos
    ) {{
        return null;
    }}

    const shape = getDirectionShape(direction);
    const start = shape.anchors[startPos];
    const end = shape.anchors[endPos];

    if (!start || !end || end.progress <= start.progress) {{
        return null;
    }}

    if (start.partIndex === end.partIndex) {{
        const coordinates = samePartSegment(
            shape.parts[start.partIndex],
            start,
            end
        );

        if (coordinates.length < 2) {{
            return null;
        }}

        return {{
            type: "LineString",
            coordinates: coordinates
        }};
    }}

    const lines = [];
    const first = firstPartSegment(
        shape.parts[start.partIndex],
        start
    );

    if (first.length >= 2) {{
        lines.push(first);
    }}

    for (
        let partIndex = start.partIndex + 1;
        partIndex < end.partIndex;
        ++partIndex
    ) {{
        const part = shape.parts[partIndex];

        if (part.length >= 2) {{
            lines.push(part);
        }}
    }}

    const last = lastPartSegment(
        shape.parts[end.partIndex],
        end
    );

    if (last.length >= 2) {{
        lines.push(last);
    }}

    if (lines.length === 0) {{
        return null;
    }}

    if (lines.length === 1) {{
        return {{
            type: "LineString",
            coordinates: lines[0]
        }};
    }}

    return {{
        type: "MultiLineString",
        coordinates: lines
    }};
}}

function routeSegmentGeometry(leg) {{
    const lines = [];

    for (const part of legParts(leg)) {{
        if (part.endPos <= part.startPos) {{
            continue;
        }}

        const geometry = routeSinglePartGeometry(part);

        if (!geometry) {{
            continue;
        }}

        lines.push(...geometryLines(geometry));
    }}

    if (lines.length === 0) {{
        return null;
    }}

    if (lines.length === 1) {{
        return {{
            type: "LineString",
            coordinates: lines[0]
        }};
    }}

    return {{
        type: "MultiLineString",
        coordinates: lines
    }};
}}

function fallbackTripLegLatLngs(leg) {{
    const result = [];

    function pushStop(stopIndex) {{
        const stop = stops[stopIndex];

        if (!stop) {{
            return;
        }}

        const latlng = [stop.lat, stop.lon];
        const last = result[result.length - 1];

        if (
            last &&
            last[0] === latlng[0] &&
            last[1] === latlng[1]
        ) {{
            return;
        }}

        result.push(latlng);
    }}

    for (const part of legParts(leg)) {{
        if (!part || !part.direction || !part.direction.stops) {{
            continue;
        }}

        const startPos = Math.max(0, Number(part.startPos) || 0);
        const endPos = Math.min(
            part.direction.stops.length - 1,
            Number(part.endPos) || 0
        );

        if (endPos < startPos) {{
            continue;
        }}

        for (let pos = startPos; pos <= endPos; ++pos) {{
            pushStop(part.direction.stops[pos]);
        }}
    }}

    return result;
}}

function tripGeometryEndpointDistanceMeters(latlng, stopIndex) {{
    const stop = stops[stopIndex];

    if (
        !stop ||
        !latlng ||
        latlng.length < 2 ||
        !Number.isFinite(latlng[0]) ||
        !Number.isFinite(latlng[1])
    ) {{
        return Infinity;
    }}

    const xy = project(latlng[1], latlng[0]);
    return Math.hypot(stop.x - xy[0], stop.y - xy[1]);
}}

function tripEdgeGeometryLines(direction, startPos, endPos) {{
    const startStopIndex = direction.stops[startPos];
    const endStopIndex = direction.stops[endPos];
    const fallback = [[
        stops[startStopIndex].lat,
        stops[startStopIndex].lon
    ], [
        stops[endStopIndex].lat,
        stops[endStopIndex].lon
    ]];

    try {{
        const geometry = routeSinglePartGeometry({{
            direction: direction,
            startPos: startPos,
            endPos: endPos
        }});

        if (!geometry) {{
            return [fallback];
        }}

        const lines = geometryLines(geometry)
            .filter(line => line && line.length >= 2)
            .map(
                line => line
                    .map(coord => [coord[1], coord[0]])
                    .filter(
                        latlng =>
                            Number.isFinite(latlng[0]) &&
                            Number.isFinite(latlng[1])
                    )
            )
            .filter(line => line.length >= 2);

        if (!lines.length) {{
            return [fallback];
        }}

        const first = lines[0][0];
        const lastLine = lines[lines.length - 1];
        const last = lastLine[lastLine.length - 1];
        const startError = tripGeometryEndpointDistanceMeters(
            first,
            startStopIndex
        );
        const endError = tripGeometryEndpointDistanceMeters(
            last,
            endStopIndex
        );

        /*
         * A route geometry can pass close to the same stop several times.
         * If map matching selected another branch of a loop, reject only
         * this stop-to-stop edge instead of corrupting the whole trip leg.
         */
        if (startError > 250.0 || endError > 250.0) {{
            console.warn(
                "Trip edge geometry rejected",
                startError,
                endError,
                startStopIndex,
                endStopIndex
            );
            return [fallback];
        }}

        return lines;
    }} catch (error) {{
        console.warn(
            "Trip edge geometry fallback",
            error,
            startStopIndex,
            endStopIndex
        );
        return [fallback];
    }}
}}

function tripLegLatLngLines(leg) {{
    const result = [];
    const route = routes[leg.routeKey];

    for (const part of legParts(leg)) {{
        if (!part || !part.direction || !part.direction.stops) {{
            continue;
        }}

        const directionBelongsToRoute =
            route &&
            route.directions &&
            route.directions.includes(part.direction);

        if (
            (part.routeKey && part.routeKey !== leg.routeKey) ||
            !directionBelongsToRoute
        ) {{
            console.error(
                "Trip leg/geometry route mismatch",
                leg.routeKey,
                part.routeKey,
                relationKey(part.direction)
            );
            continue;
        }}

        const startPos = Math.max(0, Number(part.startPos) || 0);
        const endPos = Math.min(
            part.direction.stops.length - 1,
            Number(part.endPos) || 0
        );

        for (let pos = startPos; pos < endPos; ++pos) {{
            result.push(
                ...tripEdgeGeometryLines(
                    part.direction,
                    pos,
                    pos + 1
                )
            );
        }}
    }}

    if (result.length) {{
        return result;
    }}

    const fallback = fallbackTripLegLatLngs(leg);
    return fallback.length >= 2 ? [fallback] : [];
}}

function drawTripRouteLeg(leg, color) {{
    const route = routes[leg.routeKey];

    if (!route) {{
        return false;
    }}

    const lines = tripLegLatLngLines(leg);

    if (!lines.length) {{
        console.warn("No drawable trip geometry", leg);
        return false;
    }}

    const startStopIndex = legStartStopIndex(leg);
    const endStopIndex = legEndStopIndex(leg);
    const startStop = stops[startStopIndex];
    const endStop = stops[endStopIndex];
    const popupHtml =
        startStop && endStop
            ? "<b>" +
                escapeHtml(routeLabel(route)) +
                "</b><br>" +
                escapeHtml(startStop.name || TEXT.stop) +
                " &rarr; " +
                escapeHtml(endStop.name || TEXT.stop)
            : null;

    let drawn = false;

    for (const latlngs of lines) {{
        const cleanLatLngs = latlngs.filter(
            latlng =>
                latlng &&
                latlng.length >= 2 &&
                Number.isFinite(latlng[0]) &&
                Number.isFinite(latlng[1])
        );

        if (cleanLatLngs.length < 2) {{
            continue;
        }}

        try {{
            const layer = L.polyline(
                cleanLatLngs,
                {{
                    pane: "tripRoutePane",
                    interactive: false,
                    color: color,
                    weight: 6,
                    opacity: 0.95,
                    lineCap: "round",
                    lineJoin: "round"
                }}
            );

            if (popupHtml) {{
                layer.bindPopup(popupHtml);
            }}

            layer.addTo(tripRouteLayer);
            drawn = true;
        }} catch (error) {{
            console.warn("Trip polyline pane fallback", error);

            try {{
                const fallbackLayer = L.polyline(
                    cleanLatLngs,
                    {{
                        interactive: false,
                        color: color,
                        weight: 6,
                        opacity: 0.95
                    }}
                );

                if (popupHtml) {{
                    fallbackLayer.bindPopup(popupHtml);
                }}

                fallbackLayer.addTo(tripRouteLayer);
                drawn = true;
            }} catch (fallbackError) {{
                console.error(
                    "Unable to draw trip polyline",
                    fallbackError,
                    cleanLatLngs
                );
            }}
        }}
    }}

    return drawn;
}}

function drawTripWalk(lat1, lon1, lat2, lon2) {{
    L.polyline(
        [
            [lat1, lon1],
            [lat2, lon2]
        ],
        {{
            color: "#555",
            weight: 3,
            opacity: 0.85,
            dashArray: "5 7",
            interactive: false,
            pane: "tripWalkPane"
        }}
    ).addTo(tripWalkLayer);
}}

function drawTripMarker(stopIndex, label, color) {{
    const stop = stops[stopIndex];
    const marker = L.circleMarker(
        [stop.lat, stop.lon],
        {{
            radius: 7,
            weight: 2,
            color: color,
            fillColor: "#fff",
            fillOpacity: 1,
            bubblingMouseEvents: false,
            pane: "tripMarkerPane"
        }}
    );

    marker.bindPopup(
        "<b>" +
        escapeHtml(label) +
        "</b><br>" +
        escapeHtml(stop.name || TEXT.stop) +
        "<br><br>" +
        stopRouteDetails(stopIndex)
    );

    marker.addTo(tripMarkerLayer);
}}

function drawTripPoint(point, label, color) {{
    const marker = L.circleMarker(
        [point.lat, point.lon],
        {{
            radius: 8,
            weight: 3,
            color: color,
            fillColor: "#fff",
            fillOpacity: 1,
            bubblingMouseEvents: false,
            interactive: false,
            pane: "tripMarkerPane"
        }}
    );

    marker.bindPopup("<b>" + escapeHtml(label) + "</b>");
    marker.addTo(tripMarkerLayer);

    L.circle(
        [point.lat, point.lon],
        {{
            radius: walkRadius,
            weight: 2,
            color: color,
            opacity: 0.75,
            fillColor: color,
            fillOpacity: Math.min(circleOpacity, 0.08),
            dashArray: "6 6",
            interactive: false,
            pane: "tripWalkPane"
        }}
    ).addTo(tripWalkLayer);
}}

function minutesForMeters(meters) {{
    return Math.max(0, meters / WALK_SPEED_M_PER_MIN);
}}

function addTripStep(text, className) {{
    const step = document.createElement("div");
    step.className = "trip-step " + className;
    step.innerHTML = text;
    tripSteps.appendChild(step);
}}

function legSummary(leg) {{
    const route = routes[leg.routeKey];
    const startStopIndex = legStartStopIndex(leg);
    const endStopIndex = legEndStopIndex(leg);
    const startStop = stops[startStopIndex];
    const endStop = stops[endStopIndex];
    let result = routeLabel(route);

    if (startStop || endStop) {{
        result +=
            " (" +
            (startStop && startStop.name ? startStop.name : "?") +
            " -> " +
            (endStop && endStop.name ? endStop.name : "?") +
            ")";
    }}

    return result;
}}

function itinerarySummary(itinerary, index) {{
    const prefix =
        TEXT.optionPrefix +
        String(index + 1) +
        ": ";

    prepareItineraryMetrics(itinerary);

    if (itinerary.type === "walk") {{
        return (
            prefix +
            TEXT.walkOnly +
            " ~" +
            itinerary.totalMinutes.toFixed(1) +
            " " +
            TEXT.minutesShort
        );
    }}

    let routeText = legSummary(itinerary.leg1);

    if (itinerary.type === "transfer" || itinerary.type === "two-transfer") {{
        routeText += " -> " + legSummary(itinerary.leg2);
    }}

    if (itinerary.type === "two-transfer") {{
        routeText += " -> " + legSummary(itinerary.leg3);
    }}

    return (
        prefix +
        routeText +
        ", ~" +
        itinerary.totalMinutes.toFixed(1) +
        " " +
        TEXT.minutesShort +
        ", " +
        TEXT.walkShort +
        " " +
        minutesForMeters(itinerary.walkMeters).toFixed(1) +
        " " +
        TEXT.minutesShort +
        ", " +
        String(itinerary.rideStops) +
        " " +
        TEXT.stopsShort
    );
}}

function drawTripAlternatives(
    point,
    itineraries,
    selectedIndex
) {{
    tripAlternatives.innerHTML = "";

    if (itineraries.length <= 1) {{
        return;
    }}

    itineraries.forEach(
        (itinerary, index) => {{
            const button = document.createElement("button");
            button.type = "button";
            button.className = "trip-option";
            button.textContent = itinerarySummary(
                itinerary,
                index
            );

            if (index === selectedIndex) {{
                button.classList.add("trip-option-active");
            }}

            button.addEventListener(
                "click",
                () => {{
                    tripAlternativeIndex = index;
                    renderTrip(point);
                }}
            );

            tripAlternatives.appendChild(button);
        }}
    );
}}

function addFinalWalkStep(stop, meters) {{
    if (meters <= 1.0) {{
        return;
    }}

    addTripStep(
        TEXT.walkPrefix +
        minutesForMeters(meters).toFixed(1) +
        TEXT.minutesColon +
        escapeHtml(stop.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(TEXT.destination) +
        ".",
        "trip-step-walk"
    );
}}

function renderTrip(point) {{
    clearResult();
    updateControls();

    if (!selectedPoint || !point) {{
        return;
    }}

    const itineraries = findPointItineraries(point);

    analysisSummary.hidden = true;
    tripPanel.hidden = false;
    tripSteps.innerHTML = "";

    if (tripAlternativeIndex >= itineraries.length) {{
        tripAlternativeIndex = 0;
    }}

    drawTripAlternatives(
        point,
        itineraries,
        tripAlternativeIndex
    );

    L.circleMarker(
        [selectedPoint.lat, selectedPoint.lon],
        {{
            radius: 7,
            weight: 2,
            color: "#111",
            fillColor: "#fff",
            fillOpacity: 1,
            bubblingMouseEvents: false,
            interactive: false,
            pane: "originPane"
        }}
    ).addTo(originLayer);

    drawTripPoint(point, TEXT.destination, "#16a34a");

    if (itineraries.length === 0) {{
        addTripStep(
            "No route found to the selected point for the current " +
            "walking radius and transfer mode.",
            "trip-step-walk"
        );
        return;
    }}

    const itinerary = itineraries[tripAlternativeIndex];

    if (itinerary.type === "walk") {{
        drawTripWalk(
            selectedPoint.lat,
            selectedPoint.lon,
            point.lat,
            point.lon
        );
        addTripStep(
            TEXT.walkPrefix +
            minutesForMeters(itinerary.walkMeters).toFixed(1) +
            TEXT.minutesColon +
            escapeHtml(TEXT.destination) +
            ".",
            "trip-step-walk"
        );
        return;
    }}

    const targetStopIndex = itinerary.targetStopIndex;
    const targetStop = stops[targetStopIndex];
    const leg1 = itinerary.leg1;
    const leg1StartStopIndex = legStartStopIndex(leg1);
    const leg1EndStopIndex = legEndStopIndex(leg1);
    const board = stops[leg1StartStopIndex];
    const alight1 = stops[leg1EndStopIndex];
    const route1 = routes[leg1.routeKey];

    if (!targetStop || !board || !alight1 || !route1) {{
        console.error("Invalid first trip leg", itinerary);
        addTripStep(
            "Unable to draw the selected route.",
            "trip-step-walk"
        );
        return;
    }}

    drawTripWalk(
        selectedPoint.lat,
        selectedPoint.lon,
        board.lat,
        board.lon
    );
    drawTripMarker(leg1StartStopIndex, TEXT.boarding, "#1565c0");
    drawTripRouteLeg(leg1, "#1565c0");

    addTripStep(
        TEXT.walkPrefix +
        minutesForMeters(itinerary.initialWalk).toFixed(1) +
        TEXT.walkToStop +
        escapeHtml(board.name || TEXT.stop) +
        "</b>.",
        "trip-step-walk"
    );

    addTripStep(
        "<b>" +
        escapeHtml(routeLabel(route1)) +
        "</b>: " +
        escapeHtml(board.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(alight1.name || TEXT.stop) +
        " (" +
        leg1.rideStops +
        TEXT.rideStopsSuffix,
        "trip-step-leg1"
    );

    if (itinerary.type === "direct") {{
        drawTripMarker(
            targetStopIndex,
            TEXT.destination,
            "#1565c0"
        );
        drawTripWalk(
            targetStop.lat,
            targetStop.lon,
            point.lat,
            point.lon
        );
        addFinalWalkStep(targetStop, itinerary.finalWalk || 0);
        return;
    }}

    const transferBoard = stops[itinerary.transferBoardStopIndex];
    const leg2 = itinerary.leg2;
    const leg2EndStopIndex = legEndStopIndex(leg2);
    const route2 = routes[leg2.routeKey];

    if (!transferBoard || !route2 || stops[leg2EndStopIndex] === undefined) {{
        console.error("Invalid second trip leg", itinerary);
        addTripStep(
            "Unable to draw the selected route.",
            "trip-step-walk"
        );
        return;
    }}

    drawTripMarker(
        itinerary.transferAlightStopIndex,
        TEXT.transferExit,
        "#1565c0"
    );
    drawTripMarker(
        itinerary.transferBoardStopIndex,
        TEXT.transferBoarding,
        "#ef6c00"
    );

    if (
        itinerary.transferAlightStopIndex !==
        itinerary.transferBoardStopIndex
    ) {{
        drawTripWalk(
            alight1.lat,
            alight1.lon,
            transferBoard.lat,
            transferBoard.lon
        );
    }}

    drawTripRouteLeg(leg2, "#ef6c00");

    addTripStep(
        TEXT.transferWalkPrefix +
        minutesForMeters(itinerary.transferWalk).toFixed(1) +
        TEXT.minutesColon +
        escapeHtml(alight1.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(transferBoard.name || TEXT.stop) +
        ".",
        "trip-step-walk"
    );

    const alight2 = stops[leg2EndStopIndex];

    addTripStep(
        "<b>" +
        escapeHtml(routeLabel(route2)) +
        "</b>: " +
        escapeHtml(transferBoard.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(alight2.name || TEXT.stop) +
        " (" +
        leg2.rideStops +
        TEXT.rideStopsSuffix,
        "trip-step-leg2"
    );

    if (itinerary.type === "transfer") {{
        drawTripMarker(targetStopIndex, TEXT.destination, "#ef6c00");
        drawTripWalk(
            targetStop.lat,
            targetStop.lon,
            point.lat,
            point.lon
        );
        addFinalWalkStep(targetStop, itinerary.finalWalk || 0);
        return;
    }}

    const secondTransferBoard = stops[
        itinerary.secondTransferBoardStopIndex
    ];
    const leg3 = itinerary.leg3;
    const leg3EndStopIndex = legEndStopIndex(leg3);
    const route3 = routes[leg3.routeKey];

    if (!route3 || stops[leg3EndStopIndex] === undefined) {{
        console.error("Invalid third trip leg", itinerary);
        addTripStep(
            "Unable to draw the selected route.",
            "trip-step-walk"
        );
        return;
    }}

    drawTripMarker(
        itinerary.secondTransferAlightStopIndex,
        TEXT.transferExit,
        "#ef6c00"
    );
    drawTripMarker(
        itinerary.secondTransferBoardStopIndex,
        TEXT.transferBoarding,
        "#7c3aed"
    );

    if (
        itinerary.secondTransferAlightStopIndex !==
        itinerary.secondTransferBoardStopIndex
    ) {{
        drawTripWalk(
            alight2.lat,
            alight2.lon,
            secondTransferBoard.lat,
            secondTransferBoard.lon
        );
    }}

    addTripStep(
        TEXT.transferWalkPrefix +
        minutesForMeters(itinerary.secondTransferWalk).toFixed(1) +
        TEXT.minutesColon +
        escapeHtml(alight2.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(secondTransferBoard.name || TEXT.stop) +
        ".",
        "trip-step-walk"
    );

    drawTripRouteLeg(leg3, "#7c3aed");
    drawTripMarker(targetStopIndex, TEXT.destination, "#7c3aed");

    addTripStep(
        "<b>" +
        escapeHtml(routeLabel(route3)) +
        "</b>: " +
        escapeHtml(secondTransferBoard.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(targetStop.name || TEXT.stop) +
        " (" +
        leg3.rideStops +
        TEXT.rideStopsSuffix,
        "trip-step-leg3"
    );

    drawTripWalk(
        targetStop.lat,
        targetStop.lon,
        point.lat,
        point.lon
    );
    addFinalWalkStep(targetStop, itinerary.finalWalk || 0);

}}

function showTripToPoint(point) {{
    if (!selectedPoint) {{
        return;
    }}

    targetPoint = {{
        lat: point.lat,
        lon: point.lon
    }};
    setTargetPickMode(false);
    tripAlternativeIndex = 0;
    renderTrip(targetPoint);
}}

function showTripToStop(stopIndex) {{
    if (!selectedPoint) {{
        return;
    }}

    const stop = stops[stopIndex];
    showTripToPoint({{
        lat: stop.lat,
        lon: stop.lon
    }});
}}

function selectionKey() {{
    if (!selectedPoint) {{
        return "";
    }}

    return [
        selectedPoint.lat.toFixed(6),
        selectedPoint.lon.toFixed(6),
        walkRadius.toFixed(1),
        String(maxTransfers),
        Number.isFinite(maxTravelMinutes)
            ? maxTravelMinutes.toFixed(0)
            : "inf"
    ].join(":");
}}

function addReachableRouteSegment(routeSegments, segment) {{
    if (!segment || segment.endPos <= segment.startPos) {{
        return;
    }}

    const directionId = relationKey(segment.direction);
    let startPos = segment.startPos;
    let endPos = segment.endPos;
    let startStopIndex = segment.startStopIndex;
    let endStopIndex = segment.endStopIndex;
    const removeKeys = [];

    for (const [key, current] of routeSegments.entries()) {{
        if (relationKey(current.direction) !== directionId) {{
            continue;
        }}

        if (
            endPos + 1 < current.startPos ||
            current.endPos + 1 < startPos
        ) {{
            continue;
        }}

        if (current.startPos < startPos) {{
            startPos = current.startPos;
            startStopIndex = current.startStopIndex;
        }}

        if (current.endPos > endPos) {{
            endPos = current.endPos;
            endStopIndex = current.endStopIndex;
        }}

        removeKeys.push(key);
    }}

    for (const key of removeKeys) {{
        routeSegments.delete(key);
    }}

    const key = directionId + ":" + startPos + ":" + endPos;
    routeSegments.set(key, {{
        routeKey: segment.routeKey,
        direction: segment.direction,
        startStopIndex: startStopIndex,
        endStopIndex: endStopIndex,
        startPos: startPos,
        endPos: endPos,
        rideStops: endPos - startPos
    }});
}}

function routeDirectionContinuations(route) {{
    const cached = routeContinuationCache.get(route);

    if (cached) {{
        return cached;
    }}

    const byStartStop = new Map();

    for (const direction of route.directions) {{
        if (!direction.stops || direction.stops.length === 0) {{
            continue;
        }}

        const startStopIndex = direction.stops[0];
        const list = byStartStop.get(startStopIndex) || [];
        list.push(direction);
        byStartStop.set(startStopIndex, list);
    }}

    const continuations = new Map();

    for (const direction of route.directions) {{
        if (!direction.stops || direction.stops.length === 0) {{
            continue;
        }}

        const endStopIndex = direction.stops[direction.stops.length - 1];
        continuations.set(
            relationKey(direction),
            byStartStop.get(endStopIndex) || []
        );
    }}

    routeContinuationCache.set(route, continuations);
    return continuations;
}}

function cloneRouteLegParts(parts) {{
    return parts.map(part => ({{ ...part }}));
}}

function routeLegPathKey(leg) {{
    return legParts(leg)
        .map(
            part =>
                (part.routeKey || leg.routeKey) +
                ":" +
                relationKey(part.direction) +
                ":" +
                String(part.startPos) +
                "-" +
                String(part.endPos)
        )
        .join(">");
}}

function routeLegsFromBoard(routeKey, startDirection, startPos) {{
    const cacheKey =
        routeKey +
        "|" +
        relationKey(startDirection) +
        "|" +
        String(startPos);
    const cached = routeLegTraversalCache.get(cacheKey);

    if (cached) {{
        return cached;
    }}

    const route = routes[routeKey];

    if (
        !route ||
        !startDirection.stops ||
        startPos < 0 ||
        startPos >= startDirection.stops.length
    ) {{
        return [];
    }}

    const continuations = routeDirectionContinuations(route);
    const startStopIndex = startDirection.stops[startPos];
    const initialParts = [{{
        routeKey: routeKey,
        direction: startDirection,
        startPos: startPos,
        endPos: startPos
    }}];
    const queue = [{{
        direction: startDirection,
        pos: startPos,
        parts: initialParts,
        rideStops: 0,
        rideMinutes: 0
    }}];
    const bestStateMinutes = new Map();
    const result = [];
    const EPSILON = 1e-7;

    bestStateMinutes.set(
        relationKey(startDirection) + ":" + String(startPos),
        0
    );

    while (queue.length) {{
        queue.sort((a, b) => a.rideMinutes - b.rideMinutes);
        const state = queue.shift();
        const stateKey =
            relationKey(state.direction) + ":" + String(state.pos);
        const known = bestStateMinutes.get(stateKey);

        if (
            known !== undefined &&
            state.rideMinutes > known + EPSILON
        ) {{
            continue;
        }}

        if (state.pos + 1 < state.direction.stops.length) {{
            const nextPos = state.pos + 1;
            const edge = {{
                routeKey: routeKey,
                direction: state.direction,
                startStopIndex: state.direction.stops[state.pos],
                endStopIndex: state.direction.stops[nextPos],
                startPos: state.pos,
                endPos: nextPos,
                rideStops: 1
            }};
            const edgeMinutes = legEstimatedMinutes(edge).minutes;
            const nextMinutes = state.rideMinutes + edgeMinutes;
            const nextParts = cloneRouteLegParts(state.parts);
            const lastPart = nextParts[nextParts.length - 1];
            lastPart.endPos = nextPos;
            const nextRideStops = state.rideStops + 1;
            const nextStopIndex = state.direction.stops[nextPos];
            const leg = {{
                routeKey: routeKey,
                direction: startDirection,
                startStopIndex: startStopIndex,
                endStopIndex: nextStopIndex,
                startPos: startPos,
                endPos: nextPos,
                rideStops: nextRideStops,
                parts: nextParts
            }};

            result.push(leg);

            const nextStateKey =
                relationKey(state.direction) + ":" + String(nextPos);
            const previous = bestStateMinutes.get(nextStateKey);

            if (
                previous === undefined ||
                nextMinutes + EPSILON < previous
            ) {{
                bestStateMinutes.set(nextStateKey, nextMinutes);
                queue.push({{
                    direction: state.direction,
                    pos: nextPos,
                    parts: nextParts,
                    rideStops: nextRideStops,
                    rideMinutes: nextMinutes
                }});
            }}

            continue;
        }}

        const nextDirections =
            continuations.get(relationKey(state.direction)) || [];

        for (const nextDirection of nextDirections) {{
            if (!nextDirection.stops || nextDirection.stops.length === 0) {{
                continue;
            }}

            const nextStateKey = relationKey(nextDirection) + ":0";
            const previous = bestStateMinutes.get(nextStateKey);

            if (
                previous !== undefined &&
                previous <= state.rideMinutes + EPSILON
            ) {{
                continue;
            }}

            const nextParts = cloneRouteLegParts(state.parts);
            nextParts.push({{
                routeKey: routeKey,
                direction: nextDirection,
                startPos: 0,
                endPos: 0
            }});
            bestStateMinutes.set(nextStateKey, state.rideMinutes);
            queue.push({{
                direction: nextDirection,
                pos: 0,
                parts: nextParts,
                rideStops: state.rideStops,
                rideMinutes: state.rideMinutes
            }});
        }}
    }}

    routeLegTraversalCache.set(cacheKey, result);
    return result;
}}

function collectReachableRouteData(evidence, legName) {{
    const segmentsByRoute = new Map();
    const stopsByRoute = new Map();

    for (const [stopIndex, paths] of evidence.entries()) {{
        for (const path of flattenEvidence(paths)) {{
            const leg = path[legName];

            if (!leg) {{
                continue;
            }}

            let routeStops = stopsByRoute.get(leg.routeKey);

            if (!routeStops) {{
                routeStops = new Set();
                stopsByRoute.set(leg.routeKey, routeStops);
            }}

            routeStops.add(stopIndex);

            let routeSegments = segmentsByRoute.get(leg.routeKey);

            if (!routeSegments) {{
                routeSegments = new Map();
                segmentsByRoute.set(leg.routeKey, routeSegments);
            }}

            for (const part of legParts(leg)) {{
                if (part.endPos <= part.startPos) {{
                    continue;
                }}

                addReachableRouteSegment(routeSegments, {{
                    routeKey: leg.routeKey,
                    direction: part.direction,
                    startStopIndex: part.direction.stops[part.startPos],
                    endStopIndex: part.direction.stops[part.endPos],
                    startPos: part.startPos,
                    endPos: part.endPos,
                    rideStops: part.endPos - part.startPos
                }});
            }}
        }}
    }}

    return {{
        segmentsByRoute: segmentsByRoute,
        stopsByRoute: stopsByRoute
    }};
}}

function collectBestArrivalMinutes(evidence) {{
    const result = new Map();

    if (!Number.isFinite(maxTravelMinutes)) {{
        return result;
    }}

    for (const [stopIndex, paths] of evidence.entries()) {{
        let best = Infinity;

        for (const path of flattenEvidence(paths)) {{
            prepareItineraryMetrics(path);
            best = Math.min(best, path.totalMinutes);
        }}

        if (Number.isFinite(best)) {{
            result.set(stopIndex, best);
        }}
    }}

    return result;
}}

function directionStopRole(direction, pos) {{
    if (!direction || !direction.stop_roles) {{
        return "";
    }}

    return direction.stop_roles[pos] || "";
}}

function stopAllowsBoarding(direction, pos) {{
    const role = directionStopRole(direction, pos);
    return !role.endsWith("_exit_only");
}}

function stopAllowsAlighting(direction, pos) {{
    const role = directionStopRole(direction, pos);
    return !role.endsWith("_entry_only");
}}

function normalizedStopName(stopIndex) {{
    const stop = stops[stopIndex];
    return stop && stop.name
        ? stop.name.trim().toLocaleLowerCase()
        : "";
}}

function isTechnicalSamePlaceLeg(leg) {{
    if (!leg || leg.rideStops > 2) {{
        return false;
    }}

    const startStopIndex = legStartStopIndex(leg);
    const endStopIndex = legEndStopIndex(leg);

    if (startStopIndex === endStopIndex) {{
        return true;
    }}

    const startName = normalizedStopName(startStopIndex);
    const endName = normalizedStopName(endStopIndex);

    if (!startName || startName !== endName) {{
        return false;
    }}

    return stopDistanceMeters(startStopIndex, endStopIndex) <= 300.0;
}}

function itineraryHasTechnicalSamePlaceLeg(itinerary) {{
    return candidateLegs(itinerary).some(isTechnicalSamePlaceLeg);
}}

function boardingOptionsAtStop(stopIndex) {{
    const cached = boardingOptionsCache.get(stopIndex);

    if (cached) {{
        return cached;
    }}

    const result = [];
    const stop = stops[stopIndex];

    for (const routeKey of stop.routes) {{
        const route = routes[routeKey];

        if (!route) {{
            continue;
        }}

        for (const direction of route.directions) {{
            for (const boardPos of stopPositions(direction, stopIndex)) {{
                if (!stopAllowsBoarding(direction, boardPos)) {{
                    continue;
                }}

                result.push({{
                    routeKey: routeKey,
                    direction: direction,
                    boardPos: boardPos
                }});
            }}
        }}
    }}

    boardingOptionsCache.set(stopIndex, result);
    return result;
}}

function routeEdgeMetrics(routeKey, direction, pos) {{
    const key =
        routeKey +
        "|" +
        relationKey(direction) +
        "|" +
        String(pos);
    const cached = routeEdgeMetricsCache.get(key);

    if (cached) {{
        return cached;
    }}

    if (pos < 0 || pos + 1 >= direction.stops.length) {{
        return null;
    }}

    const edge = {{
        routeKey: routeKey,
        direction: direction,
        startStopIndex: direction.stops[pos],
        endStopIndex: direction.stops[pos + 1],
        startPos: pos,
        endPos: pos + 1,
        rideStops: 1
    }};
    const metrics = legEstimatedMinutes(edge);
    routeEdgeMetricsCache.set(key, metrics);
    return metrics;
}}

/*
 * RAPTOR-style reachability engine.
 * Round 0 = first ride, round 1 = one transfer, round 2 = two transfers.
 * Travel-time changes only filter cached labels; increasing the transfer
 * limit computes only the missing round.
 */
function reachabilitySearchKey() {{
    if (!selectedPoint) {{
        return "";
    }}

    return [
        selectedPoint.lat.toFixed(6),
        selectedPoint.lon.toFixed(6),
        walkRadius.toFixed(1)
    ].join(":");
}}

function cloneLegParts(parts) {{
    return parts.map(part => ({{ ...part }}));
}}

function initialRideLeg(routeKey, direction, boardPos) {{
    const stopIndex = direction.stops[boardPos];

    return {{
        routeKey: routeKey,
        direction: direction,
        startStopIndex: stopIndex,
        endStopIndex: stopIndex,
        startPos: boardPos,
        endPos: boardPos,
        rideStops: 0,
        parts: [{{
            routeKey: routeKey,
            direction: direction,
            startPos: boardPos,
            endPos: boardPos
        }}]
    }};
}}

function extendRideLeg(leg, direction, nextPos) {{
    const parts = cloneLegParts(leg.parts);
    const last = parts[parts.length - 1];
    last.endPos = nextPos;

    return {{
        ...leg,
        endStopIndex: direction.stops[nextPos],
        endPos: nextPos,
        rideStops: leg.rideStops + 1,
        parts: parts
    }};
}}

function continueRideLeg(leg, nextDirection) {{
    const parts = cloneLegParts(leg.parts);
    parts.push({{
        routeKey: leg.routeKey,
        direction: nextDirection,
        startPos: 0,
        endPos: 0
    }});

    return {{
        ...leg,
        endStopIndex: nextDirection.stops[0],
        endPos: 0,
        parts: parts
    }};
}}

function itineraryFromRaptorState(state) {{
    const legs = state.legs.concat([state.currentLeg]);
    const transferCount = state.transfersUsed;
    const walkMinutes = state.walkMeters / WALK_SPEED_M_PER_MIN;
    const candidate = {{
        type:
            transferCount === 0
                ? "direct"
                : transferCount === 1
                    ? "transfer"
                    : "two-transfer",
        walkMeters: state.walkMeters,
        walkMinutes: walkMinutes,
        rideStops: state.rideStops,
        initialWalk: state.initialWalk,
        transitMeters: state.transitMeters,
        boardings: transferCount + 1,
        totalMinutes: state.elapsedMinutes,
        generalizedMinutes:
            state.elapsedMinutes + walkMinutes * WALK_PREFERENCE_EXTRA,
        leg1: legs[0]
    }};

    if (transferCount >= 1) {{
        const transfer = state.transfers[0];
        candidate.leg2 = legs[1];
        candidate.transferWalk = transfer.walkMeters;
        candidate.transferAlightStopIndex = transfer.alightStopIndex;
        candidate.transferBoardStopIndex = transfer.boardStopIndex;
    }}

    if (transferCount >= 2) {{
        const transfer = state.transfers[1];
        candidate.leg3 = legs[2];
        candidate.secondTransferWalk = transfer.walkMeters;
        candidate.secondTransferAlightStopIndex =
            transfer.alightStopIndex;
        candidate.secondTransferBoardStopIndex =
            transfer.boardStopIndex;
    }}

    return candidate;
}}

function candidateLegs(candidate) {{
    const result = [];

    if (candidate.leg1) {{
        result.push(candidate.leg1);
    }}

    if (candidate.leg2) {{
        result.push(candidate.leg2);
    }}

    if (candidate.leg3) {{
        result.push(candidate.leg3);
    }}

    return result;
}}

function candidateTransfers(candidate) {{
    const result = [];

    if (candidate.leg2) {{
        result.push({{
            alightStopIndex: candidate.transferAlightStopIndex,
            boardStopIndex: candidate.transferBoardStopIndex,
            walkMeters: candidate.transferWalk || 0
        }});
    }}

    if (candidate.leg3) {{
        result.push({{
            alightStopIndex:
                candidate.secondTransferAlightStopIndex,
            boardStopIndex:
                candidate.secondTransferBoardStopIndex,
            walkMeters: candidate.secondTransferWalk || 0
        }});
    }}

    return result;
}}

function searchEvidencePathKey(candidate) {{
    return candidateLegs(candidate)
        .map(
            leg =>
                leg.routeKey +
                ":" +
                String(legStartStopIndex(leg)) +
                "-" +
                String(legEndStopIndex(leg)) +
                ":" +
                routeLegPathKey(leg)
        )
        .join(">");
}}

function updateSearchEvidence(evidence, stopIndex, candidate) {{
    const pathKey = searchEvidencePathKey(candidate);
    let byPath = evidence.get(stopIndex);

    if (!byPath) {{
        byPath = new Map();
        evidence.set(stopIndex, byPath);
    }}

    let bucket = byPath.get(pathKey) || [];

    if (bucket.some(item => itineraryDominates(item, candidate))) {{
        return;
    }}

    bucket = bucket.filter(
        item => !itineraryDominates(candidate, item)
    );
    bucket.push(candidate);
    bucket.sort(compareItineraries);

    if (bucket.length > MAX_EVIDENCE_PER_PATH) {{
        bucket = bucket.slice(0, MAX_EVIDENCE_PER_PATH);
    }}

    byPath.set(pathKey, bucket);
}}

function raptorSourcePathKey(source) {{
    if (!source.legs || source.legs.length === 0) {{
        return "walk";
    }}

    return source.legs
        .map(
            leg =>
                leg.routeKey +
                ":" +
                String(legStartStopIndex(leg)) +
                "-" +
                String(legEndStopIndex(leg))
        )
        .join(">");
}}

function raptorSourceDominates(a, b) {{
    const noSlower = a.elapsedMinutes <= b.elapsedMinutes + 1e-7;
    const noMoreWalk = a.walkMeters <= b.walkMeters + 1.0;
    const strictlyBetter =
        a.elapsedMinutes < b.elapsedMinutes - 1e-7 ||
        a.walkMeters < b.walkMeters - 1.0;

    return noSlower && noMoreWalk && strictlyBetter;
}}

function raptorSourceScore(source) {{
    return (
        source.elapsedMinutes +
        source.walkMeters /
            WALK_SPEED_M_PER_MIN * WALK_PREFERENCE_EXTRA
    );
}}

function addRaptorTransferSource(
    sourcesByStop,
    stopIndex,
    source
) {{
    let byLastRoute = sourcesByStop.get(stopIndex);

    if (!byLastRoute) {{
        byLastRoute = new Map();
        sourcesByStop.set(stopIndex, byLastRoute);
    }}

    const lastRouteKey = source.lastRouteKey || "";
    let bucket = byLastRoute.get(lastRouteKey) || [];

    if (bucket.some(item => raptorSourceDominates(item, source))) {{
        return false;
    }}

    bucket = bucket.filter(
        item => !raptorSourceDominates(source, item)
    );

    const pathKey = raptorSourcePathKey(source);
    const samePathIndex = bucket.findIndex(
        item => raptorSourcePathKey(item) === pathKey
    );

    if (samePathIndex >= 0) {{
        const current = bucket[samePathIndex];

        if (raptorSourceScore(current) <= raptorSourceScore(source)) {{
            return false;
        }}

        bucket.splice(samePathIndex, 1);
    }}

    bucket.push(source);
    bucket.sort(
        (a, b) => raptorSourceScore(a) - raptorSourceScore(b)
    );

    if (bucket.length > MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE) {{
        bucket = bucket.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE);
    }}

    byLastRoute.set(lastRouteKey, bucket);

    const all = [];

    for (const [routeKey, routeBucket] of byLastRoute.entries()) {{
        for (const item of routeBucket) {{
            all.push({{ routeKey: routeKey, source: item }});
        }}
    }}

    if (all.length > MAX_RAPTOR_TRANSFER_LABELS_PER_STOP) {{
        all.sort(
            (a, b) =>
                raptorSourceScore(a.source) -
                raptorSourceScore(b.source)
        );
        const keep = new Set(
            all
                .slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_STOP)
                .map(item => item.source)
        );

        for (const [routeKey, routeBucket] of byLastRoute.entries()) {{
            const filtered = routeBucket.filter(item => keep.has(item));

            if (filtered.length) {{
                byLastRoute.set(routeKey, filtered);
            }} else {{
                byLastRoute.delete(routeKey);
            }}
        }}
    }}

    return true;
}}

function flattenRaptorSources(byLastRoute) {{
    const result = [];

    if (!byLastRoute) {{
        return result;
    }}

    for (const bucket of byLastRoute.values()) {{
        result.push(...bucket);
    }}

    return result;
}}

function raptorOnboardNodeKey(state) {{
    return [
        state.routeKey,
        relationKey(state.direction),
        String(state.pos)
    ].join("|");
}}

function raptorOnboardPathKey(state) {{
    return [
        ...state.legs.map(leg => leg.routeKey),
        state.routeKey,
        String(legStartStopIndex(state.currentLeg))
    ].join(">");
}}

function raptorOnboardDominates(a, b) {{
    const noSlower = a.elapsedMinutes <= b.elapsedMinutes + 1e-7;
    const noMoreWalk = a.walkMeters <= b.walkMeters + 1.0;
    const strictlyBetter =
        a.elapsedMinutes < b.elapsedMinutes - 1e-7 ||
        a.walkMeters < b.walkMeters - 1.0;

    return noSlower && noMoreWalk && strictlyBetter;
}}

function raptorOnboardScore(state) {{
    return (
        state.elapsedMinutes +
        state.walkMeters /
            WALK_SPEED_M_PER_MIN * WALK_PREFERENCE_EXTRA
    );
}}

function addRaptorOnboardState(nodeLabels, queue, state) {{
    const nodeKey = raptorOnboardNodeKey(state);
    let bucket = nodeLabels.get(nodeKey) || [];

    if (bucket.some(item => raptorOnboardDominates(item, state))) {{
        return false;
    }}

    bucket = bucket.filter(
        item => !raptorOnboardDominates(state, item)
    );

    const pathKey = raptorOnboardPathKey(state);
    const samePathIndex = bucket.findIndex(
        item => raptorOnboardPathKey(item) === pathKey
    );

    if (samePathIndex >= 0) {{
        const current = bucket[samePathIndex];

        if (raptorOnboardScore(current) <= raptorOnboardScore(state)) {{
            return false;
        }}

        bucket.splice(samePathIndex, 1);
    }}

    bucket.push(state);
    bucket.sort(
        (a, b) => raptorOnboardScore(a) - raptorOnboardScore(b)
    );

    if (bucket.length > MAX_RAPTOR_ONBOARD_LABELS) {{
        bucket = bucket.slice(0, MAX_RAPTOR_ONBOARD_LABELS);
    }}

    if (!bucket.includes(state)) {{
        nodeLabels.set(nodeKey, bucket);
        return false;
    }}

    nodeLabels.set(nodeKey, bucket);
    queue.push(state);
    return true;
}}

function seedRaptorRound(boardSources, roundIndex) {{
    const nodeLabels = new Map();
    const queue = [];

    for (const [boardStopIndex, byLastRoute] of boardSources.entries()) {{
        const sources = flattenRaptorSources(byLastRoute);

        for (const source of sources) {{
            for (const option of boardingOptionsAtStop(boardStopIndex)) {{
                if (
                    source.lastRouteKey &&
                    source.lastRouteKey === option.routeKey
                ) {{
                    continue;
                }}

                addRaptorOnboardState(nodeLabels, queue, {{
                    routeKey: option.routeKey,
                    direction: option.direction,
                    pos: option.boardPos,
                    transfersUsed: roundIndex,
                    elapsedMinutes:
                        source.elapsedMinutes + BOARDING_WAIT_MIN,
                    walkMeters: source.walkMeters,
                    initialWalk: source.initialWalk,
                    rideStops: source.rideStops,
                    transitMeters: source.transitMeters,
                    legs: source.legs,
                    currentLeg: initialRideLeg(
                        option.routeKey,
                        option.direction,
                        option.boardPos
                    ),
                    transfers: source.transfers
                }});
            }}
        }}
    }}

    return {{ nodeLabels: nodeLabels, queue: queue }};
}}

function scanRaptorRound(boardSources, roundIndex) {{
    const seeded = seedRaptorRound(boardSources, roundIndex);
    const nodeLabels = seeded.nodeLabels;
    const queue = seeded.queue;
    const evidence = new Map();
    let head = 0;

    while (head < queue.length) {{
        const state = queue[head++];
        const nodeKey = raptorOnboardNodeKey(state);
        const currentBucket = nodeLabels.get(nodeKey);

        if (!currentBucket || !currentBucket.includes(state)) {{
            continue;
        }}

        const currentStopIndex = state.direction.stops[state.pos];

        if (
            state.currentLeg.rideStops > 0 &&
            stopAllowsAlighting(state.direction, state.pos)
        ) {{
            updateSearchEvidence(
                evidence,
                currentStopIndex,
                itineraryFromRaptorState(state)
            );
        }}

        if (state.pos + 1 < state.direction.stops.length) {{
            const metrics = routeEdgeMetrics(
                state.routeKey,
                state.direction,
                state.pos
            );

            if (!metrics) {{
                continue;
            }}

            const nextPos = state.pos + 1;
            addRaptorOnboardState(nodeLabels, queue, {{
                ...state,
                pos: nextPos,
                elapsedMinutes:
                    state.elapsedMinutes + metrics.minutes,
                rideStops: state.rideStops + 1,
                transitMeters:
                    state.transitMeters + metrics.distance,
                currentLeg: extendRideLeg(
                    state.currentLeg,
                    state.direction,
                    nextPos
                )
            }});
            continue;
        }}

        const route = routes[state.routeKey];
        const continuations = routeDirectionContinuations(route);

        for (const nextDirection of
            continuations.get(relationKey(state.direction)) || []) {{
            if (
                !nextDirection.stops ||
                nextDirection.stops.length === 0
            ) {{
                continue;
            }}

            addRaptorOnboardState(nodeLabels, queue, {{
                ...state,
                direction: nextDirection,
                pos: 0,
                currentLeg: continueRideLeg(
                    state.currentLeg,
                    nextDirection
                )
            }});
        }}
    }}

    return evidence;
}}

function bestRaptorTransferCandidates(byPath) {{
    const byLastRoute = new Map();

    for (const candidate of flattenEvidence(byPath)) {{
        const legs = candidateLegs(candidate);
        const lastLeg = legs[legs.length - 1];

        if (!lastLeg) {{
            continue;
        }}

        const routeKey = lastLeg.routeKey;
        let bucket = byLastRoute.get(routeKey) || [];

        if (bucket.some(item => itineraryDominates(item, candidate))) {{
            continue;
        }}

        bucket = bucket.filter(
            item => !itineraryDominates(candidate, item)
        );
        bucket.push(candidate);
        bucket.sort(compareItineraries);

        if (bucket.length > MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE) {{
            bucket = bucket.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE);
        }}

        byLastRoute.set(routeKey, bucket);
    }}

    const result = [];

    for (const bucket of byLastRoute.values()) {{
        result.push(...bucket);
    }}

    result.sort(compareItineraries);
    return result.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_STOP);
}}

function buildRaptorTransferSources(evidence) {{
    const result = new Map();

    for (const [alightStopIndex, byPath] of evidence.entries()) {{
        const candidates = bestRaptorTransferCandidates(byPath);

        for (const candidate of candidates) {{
            prepareItineraryMetrics(candidate);
            const legs = candidateLegs(candidate);
            const lastLeg = legs[legs.length - 1];

            if (!lastLeg) {{
                continue;
            }}

            for (const boardStopIndex of
                findNearbyStopsFromStop(alightStopIndex)) {{
                const transferWalk = stopDistanceMeters(
                    alightStopIndex,
                    boardStopIndex
                );
                const transferMinutes =
                    transferWalk / WALK_SPEED_M_PER_MIN;
                const source = {{
                    elapsedMinutes:
                        candidate.totalMinutes + transferMinutes,
                    walkMeters:
                        candidate.walkMeters + transferWalk,
                    initialWalk: candidate.initialWalk,
                    rideStops: candidate.rideStops,
                    transitMeters: candidate.transitMeters,
                    legs: legs,
                    transfers: candidateTransfers(candidate).concat([{{
                        alightStopIndex: alightStopIndex,
                        boardStopIndex: boardStopIndex,
                        walkMeters: transferWalk
                    }}]),
                    lastRouteKey: lastLeg.routeKey
                }};

                addRaptorTransferSource(
                    result,
                    boardStopIndex,
                    source
                );
            }}
        }}
    }}

    return result;
}}

function createInitialRaptorSources(nearbyStops) {{
    const result = new Map();

    for (const boardStopIndex of nearbyStops) {{
        const initialWalk = pointToStopDistanceMeters(
            selectedPoint,
            boardStopIndex
        );

        addRaptorTransferSource(result, boardStopIndex, {{
            elapsedMinutes: initialWalk / WALK_SPEED_M_PER_MIN,
            walkMeters: initialWalk,
            initialWalk: initialWalk,
            rideStops: 0,
            transitMeters: 0,
            legs: [],
            transfers: [],
            lastRouteKey: null
        }});
    }}

    return result;
}}

function createReachabilitySearch() {{
    const nearbyStops = findNearbyStops(
        selectedPoint.lat,
        selectedPoint.lon
    );

    return {{
        nearbyStops: nearbyStops,
        evidence: [new Map(), new Map(), new Map()],
        boardSources: createInitialRaptorSources(nearbyStops),
        builtRounds: 0
    }};
}}

function ensureReachabilitySearch() {{
    const cacheKey = reachabilitySearchKey();
    let context = reachabilitySearchCache.get(cacheKey);

    if (!context) {{
        context = createReachabilitySearch();
        reachabilitySearchCache.set(cacheKey, context);

        if (reachabilitySearchCache.size > 4) {{
            const firstKey = reachabilitySearchCache.keys().next().value;
            reachabilitySearchCache.delete(firstKey);
        }}
    }}

    const requiredRounds = Math.min(
        RAPTOR_ROUNDS,
        maxTransfers + 1
    );

    while (context.builtRounds < requiredRounds) {{
        const round = context.builtRounds;

        if (!context.boardSources || context.boardSources.size === 0) {{
            context.builtRounds = RAPTOR_ROUNDS;
            break;
        }}

        context.evidence[round] = scanRaptorRound(
            context.boardSources,
            round
        );
        ++context.builtRounds;

        if (context.builtRounds < RAPTOR_ROUNDS) {{
            context.boardSources = buildRaptorTransferSources(
                context.evidence[round]
            );
        }}
    }}

    return context;
}}

function filterEvidenceByTravelLimit(evidence) {{
    if (!Number.isFinite(maxTravelMinutes)) {{
        return evidence;
    }}

    const result = new Map();

    for (const [stopIndex, byPath] of evidence.entries()) {{
        const filteredPaths = new Map();

        for (const [pathKey, bucket] of byPath.entries()) {{
            const filtered = bucket.filter(
                candidate =>
                    candidate.totalMinutes <= maxTravelMinutes + 1e-7
            );

            if (filtered.length > 0) {{
                filteredPaths.set(pathKey, filtered);
            }}
        }}

        if (filteredPaths.size > 0) {{
            result.set(stopIndex, filteredPaths);
        }}
    }}

    return result;
}}

function analyzeSelection() {{
    const key = selectionKey();

    if (key === analysisCacheKey && analysisCache) {{
        return analysisCache;
    }}

    const search = ensureReachabilitySearch();
    const nearbyStops = search.nearbyStops;
    const directEvidence = filterEvidenceByTravelLimit(
        search.evidence[0]
    );
    const transferEvidence = maxTransfers >= 1
        ? filterEvidenceByTravelLimit(search.evidence[1])
        : new Map();
    const twoTransferEvidence = maxTransfers >= 2
        ? filterEvidenceByTravelLimit(search.evidence[2])
        : new Map();
    const directRoutes = new Set();
    const directDirectionIds = new Set();

    for (const paths of directEvidence.values()) {{
        for (const path of flattenEvidence(paths)) {{
            directRoutes.add(path.leg1.routeKey);
            directDirectionIds.add(relationKey(path.leg1.direction));
        }}
    }}

    const directStops = new Set(directEvidence.keys());
    const directTransitStops = new Set(directStops);
    const transferBoardingStops = new Set();
    const transferRoutes = new Set();
    const transferDirectionIds = new Set();
    const transferTransitStops = new Set(
        transferEvidence.keys()
    );
    const transferOnlyStops = new Set();

    for (const [stopIndex, paths] of transferEvidence.entries()) {{
        if (!directStops.has(stopIndex)) {{
            transferOnlyStops.add(stopIndex);
        }}

        for (const path of flattenEvidence(paths)) {{
            transferBoardingStops.add(path.transferBoardStopIndex);
            transferRoutes.add(path.leg2.routeKey);
            transferDirectionIds.add(
                relationKey(path.leg2.direction)
            );
        }}
    }}

    const twoTransferBoardingStops = new Set();
    const twoTransferRoutes = new Set();
    const twoTransferDirectionIds = new Set();
    const twoTransferTransitStops = new Set(
        twoTransferEvidence.keys()
    );
    const twoTransferOnlyStops = new Set();

    for (const [stopIndex, paths] of twoTransferEvidence.entries()) {{
        if (
            !directStops.has(stopIndex) &&
            !transferEvidence.has(stopIndex)
        ) {{
            twoTransferOnlyStops.add(stopIndex);
        }}

        for (const path of flattenEvidence(paths)) {{
            twoTransferBoardingStops.add(
                path.secondTransferBoardStopIndex
            );
            twoTransferRoutes.add(path.leg3.routeKey);
            twoTransferDirectionIds.add(
                relationKey(path.leg3.direction)
            );
        }}
    }}

    const directRouteData = collectReachableRouteData(
        directEvidence,
        "leg1"
    );
    const transferRouteData = collectReachableRouteData(
        transferEvidence,
        "leg2"
    );
    const twoTransferRouteData = collectReachableRouteData(
        twoTransferEvidence,
        "leg3"
    );
    const directArrivalMinutes = collectBestArrivalMinutes(
        directEvidence
    );
    const transferArrivalMinutes = collectBestArrivalMinutes(
        transferEvidence
    );
    const twoTransferArrivalMinutes = collectBestArrivalMinutes(
        twoTransferEvidence
    );

    analysisCacheKey = key;
    analysisCache = {{
        nearbyStops: nearbyStops,
        directRoutes: directRoutes,
        directDirectionIds: directDirectionIds,
        directTransitStops: directTransitStops,
        directStops: directStops,
        directEvidence: directEvidence,
        directRouteSegments: directRouteData.segmentsByRoute,
        directRouteStops: directRouteData.stopsByRoute,
        directArrivalMinutes: directArrivalMinutes,
        transferBoardingStops: transferBoardingStops,
        transferRoutes: transferRoutes,
        transferDirectionIds: transferDirectionIds,
        transferTransitStops: transferTransitStops,
        transferOnlyStops: transferOnlyStops,
        transferEvidence: transferEvidence,
        transferRouteSegments: transferRouteData.segmentsByRoute,
        transferRouteStops: transferRouteData.stopsByRoute,
        transferArrivalMinutes: transferArrivalMinutes,
        twoTransferBoardingStops: twoTransferBoardingStops,
        twoTransferRoutes: twoTransferRoutes,
        twoTransferDirectionIds: twoTransferDirectionIds,
        twoTransferTransitStops: twoTransferTransitStops,
        twoTransferOnlyStops: twoTransferOnlyStops,
        twoTransferEvidence: twoTransferEvidence,
        twoTransferRouteSegments: twoTransferRouteData.segmentsByRoute,
        twoTransferRouteStops: twoTransferRouteData.stopsByRoute,
        twoTransferArrivalMinutes: twoTransferArrivalMinutes
    }};

    return analysisCache;
}}

function appendRouteGroup(container, title, routeKeys, transferCount) {{
    if (routeKeys.size === 0) {{
        return;
    }}

    const heading = document.createElement("div");
    heading.className = "route-group-title";

    if (transferCount === 0) {{
        heading.classList.add("route-group-title-direct");
    }}

    heading.textContent = title;
    container.appendChild(heading);

    const ordered = Array.from(routeKeys);

    ordered.sort(
        (a, b) => {{
            const ar = routes[a];
            const br = routes[b];
            const aa = ar && ar.ref ? ar.ref : "";
            const bb = br && br.ref ? br.ref : "";

            return aa.localeCompare(
                bb,
                undefined,
                {{
                    numeric: true
                }}
            );
        }}
    );

    for (const key of ordered) {{
        const route = routes[key];

        if (!route) {{
            continue;
        }}

        const item = document.createElement("span");
        item.className = "route-chip";
        item.style.background = routeColor(key);
        item.style.opacity = ["1", "0.65", "0.5"][transferCount];
        item.textContent = routeLabel(route);
        item.style.cursor = "pointer";

        if (
            selectedRouteFocus &&
            selectedRouteFocus.routeKey === key &&
            selectedRouteFocus.transferCount === transferCount
        ) {{
            item.style.outline = "3px solid #111";
            item.style.outlineOffset = "2px";
        }}

        item.addEventListener(
            "click",
            () => {{
                const same =
                    selectedRouteFocus &&
                    selectedRouteFocus.routeKey === key &&
                    selectedRouteFocus.transferCount === transferCount;

                selectedRouteFocus = same
                    ? null
                    : {{ routeKey: key, transferCount: transferCount }};
                renderSelection();
            }}
        );

        container.appendChild(item);
    }}
}}

function focusedRouteStops(analysis) {{
    if (!selectedRouteFocus) {{
        return null;
    }}

    const stopMaps = [
        analysis.directRouteStops,
        analysis.transferRouteStops,
        analysis.twoTransferRouteStops
    ];
    const routeStops = stopMaps[
        selectedRouteFocus.transferCount
    ].get(selectedRouteFocus.routeKey);

    if (!routeStops) {{
        selectedRouteFocus = null;
        return null;
    }}

    return routeStops;
}}

function isFocusedRoute(routeKey, transferCount) {{
    return (
        selectedRouteFocus &&
        selectedRouteFocus.routeKey === routeKey &&
        selectedRouteFocus.transferCount === transferCount
    );
}}

function drawFocusedRouteStops(analysis) {{
    const routeStops = focusedRouteStops(analysis);

    if (!routeStops || !selectedRouteFocus) {{
        return;
    }}

    const color = routeColor(selectedRouteFocus.routeKey);

    for (const stopIndex of routeStops) {{
        const stop = stops[stopIndex];
        const marker = L.circleMarker(
            [stop.lat, stop.lon],
            {{
                radius: 7,
                weight: 3,
                color: color,
                fillColor: "#fff",
                fillOpacity: 1,
                bubblingMouseEvents: false,
                pane: "stopPane"
            }}
        );

        marker.bindPopup(
            "<b>" +
            escapeHtml(stop.name || TEXT.stop) +
            "</b><br>" +
            escapeHtml(routeLabel(routes[selectedRouteFocus.routeKey])) +
            "<br><span style='color:#666'>" +
            TEXT.doubleClick +
            "</span>"
        );

        marker.on(
            "click",
            event => {{
                suppressMapClickUntil = performance.now() + 600;

                if (event.originalEvent) {{
                    L.DomEvent.stop(event.originalEvent);
                }}
            }}
        );

        marker.on(
            "dblclick",
            event => {{
                suppressMapClickUntil = performance.now() + 600;

                if (event.originalEvent) {{
                    L.DomEvent.stop(event.originalEvent);
                }}

                marker.closePopup();
                showTripToStop(stopIndex);
            }}
        );

        marker.addTo(stopLayer);
    }}
}}

function updateRouteList(
    directRoutes,
    transferRoutes,
    twoTransferRoutes
) {{
    const container = document.getElementById("routes");
    container.innerHTML = "";

    appendRouteGroup(
        container,
        TEXT.groupDirect,
        directRoutes,
        0
    );

    if (maxTransfers >= 1) {{
        appendRouteGroup(
            container,
            TEXT.groupTransfer,
            transferRoutes,
            1
        );
    }}

    if (maxTransfers >= 2) {{
        appendRouteGroup(
            container,
            TEXT.groupTwoTransfers,
            twoTransferRoutes,
            2
        );
    }}
}}

function renderSelection() {{
    clearResult();
    updateControls();

    if (!selectedPoint) {{
        return;
    }}

    analysisSummary.hidden = false;
    tripPanel.hidden = true;

    drawOrigin(selectedPoint.lat, selectedPoint.lon);

    const analysis = analyzeSelection();
    const focusStops = focusedRouteStops(analysis);
    const hasFocus = !!selectedRouteFocus && !!focusStops;

    if (maxTransfers >= 2) {{
        for (const routeKey of analysis.twoTransferRoutes) {{
            if (hasFocus && !isFocusedRoute(routeKey, 2)) {{
                continue;
            }}

            drawRoute(
                routeKey,
                2,
                analysis.twoTransferRouteSegments.get(routeKey)
            );
        }}

        if (!hasFocus) {{
            for (const stopIndex of analysis.twoTransferOnlyStops) {{
                drawCoverage(
                    stops[stopIndex],
                    2,
                    analysis.twoTransferArrivalMinutes.get(stopIndex)
                );
                drawStop(stopIndex, 2);
            }}
        }}
    }}

    if (maxTransfers >= 1) {{
        for (const routeKey of analysis.transferRoutes) {{
            if (hasFocus && !isFocusedRoute(routeKey, 1)) {{
                continue;
            }}

            drawRoute(
                routeKey,
                1,
                analysis.transferRouteSegments.get(routeKey)
            );
        }}

        if (!hasFocus) {{
            for (const stopIndex of analysis.transferOnlyStops) {{
                drawCoverage(
                    stops[stopIndex],
                    1,
                    analysis.transferArrivalMinutes.get(stopIndex)
                );
                drawStop(stopIndex, 1);
            }}
        }}
    }}

    for (const routeKey of analysis.directRoutes) {{
        if (hasFocus && !isFocusedRoute(routeKey, 0)) {{
            continue;
        }}

        drawRoute(
            routeKey,
            0,
            analysis.directRouteSegments.get(routeKey)
        );
    }}

    if (!hasFocus) {{
        for (const stopIndex of analysis.directStops) {{
            drawCoverage(
                stops[stopIndex],
                0,
                analysis.directArrivalMinutes.get(stopIndex)
            );
            drawStop(stopIndex, 0);
        }}
    }}

    if (!hasFocus) {{
        flushCoverage();
    }}

    document.getElementById(
        "nearby-count"
    ).textContent = analysis.nearbyStops.length;

    document.getElementById(
        "direct-route-count"
    ).textContent = analysis.directRoutes.size;

    document.getElementById(
        "direct-stop-count"
    ).textContent = analysis.directStops.size;

    document.getElementById(
        "transfer-route-count"
    ).textContent = maxTransfers >= 1
        ? analysis.transferRoutes.size
        : 0;

    document.getElementById(
        "transfer-stop-count"
    ).textContent = maxTransfers >= 1
        ? analysis.transferOnlyStops.size
        : 0;

    document.getElementById(
        "two-transfer-route-count"
    ).textContent = maxTransfers >= 2
        ? analysis.twoTransferRoutes.size
        : 0;

    document.getElementById(
        "two-transfer-stop-count"
    ).textContent = maxTransfers >= 2
        ? analysis.twoTransferOnlyStops.size
        : 0;

    if (hasFocus) {{
        drawFocusedRouteStops(analysis);
    }}

    updateRouteList(
        analysis.directRoutes,
        analysis.transferRoutes,
        analysis.twoTransferRoutes
    );
}}

function scheduleRender() {{
    updateControls();

    if (!selectedPoint) {{
        return;
    }}

    if (renderFrame !== null) {{
        cancelAnimationFrame(renderFrame);
    }}

    renderFrame = requestAnimationFrame(
        () => {{
            renderFrame = null;

            if (targetPoint) {{
                renderTrip(targetPoint);
            }} else {{
                renderSelection();
            }}
        }}
    );
}}

walkTimeInput.addEventListener(
    "input",
    scheduleRender
);

transferCountInput.addEventListener(
    "input",
    scheduleRender
);

travelTimeLimitInput.addEventListener(
    "input",
    () => {{
        updateControls();

        if (travelRenderTimer !== null) {{
            clearTimeout(travelRenderTimer);
        }}

        travelRenderTimer = setTimeout(
            () => {{
                travelRenderTimer = null;
                scheduleRender();
            }},
            70
        );
    }}
);

map.on("zoomend", () => {{
    redrawCoverageOverlay();
    updateStopMarkerSizes();
}});

map.on("resize", () => {{
    redrawCoverageOverlay();
}});

circleOpacityInput.addEventListener(
    "input",
    () => {{
        circleOpacity = Number(circleOpacityInput.value) / 100.0;
        circleOpacityValue.textContent =
            Math.round(circleOpacity * 100) + "%";
        coverageGroup.setAttribute("opacity", String(circleOpacity));
    }}
);

tripBack.addEventListener(
    "click",
    () => {{
        targetPoint = null;
        setTargetPickMode(false);
        tripAlternativeIndex = 0;
        renderSelection();
    }}
);

function selectStartPoint(latlng) {{
    selectedPoint = {{
        lat: latlng.lat,
        lon: latlng.lng
    }};
    targetPoint = null;
    setTargetPickMode(false);
    tripAlternativeIndex = 0;
    selectedRouteFocus = null;

    analysisCacheKey = null;
    analysisCache = null;
    reachabilitySearchCache.clear();
    itineraryCache.clear();
    renderSelection();
}}

function cancelPendingMapClick() {{
    if (pendingMapClickTimer === null) {{
        return;
    }}

    clearTimeout(pendingMapClickTimer);
    pendingMapClickTimer = null;
}}

map.on(
    "click",
    event => {{
        if (targetPickMode && selectedPoint) {{
            cancelPendingMapClick();
            suppressMapClickUntil = performance.now() + 600;
            showTripToPoint({{
                lat: event.latlng.lat,
                lon: event.latlng.lng
            }});
            return;
        }}

        const stopIndex = nearestDisplayedStop(
            event.latlng,
            14
        );

        if (stopIndex !== null) {{
            cancelPendingMapClick();
            suppressMapClickUntil = performance.now() + 600;
            openStopPopup(stopIndex);
            return;
        }}

        if (performance.now() < suppressMapClickUntil) {{
            return;
        }}

        if (event.originalEvent && event.originalEvent._stopped) {{
            return;
        }}

        const latlng = {{
            lat: event.latlng.lat,
            lng: event.latlng.lng
        }};

        cancelPendingMapClick();
        pendingMapClickTimer = setTimeout(
            () => {{
                pendingMapClickTimer = null;
                selectStartPoint(latlng);
            }},
            MAP_DOUBLE_CLICK_DELAY_MS
        );
    }}
);

map.on(
    "dblclick",
    event => {{
        cancelPendingMapClick();
        suppressMapClickUntil = performance.now() + 600;

        if (event.originalEvent) {{
            L.DomEvent.stop(event.originalEvent);
        }}

        if (!selectedPoint) {{
            selectStartPoint(event.latlng);
            return;
        }}

        tripAlternativeIndex = 0;
        showTripToPoint({{
            lat: event.latlng.lat,
            lon: event.latlng.lng
        }});
    }}
);

updateControls();
}})().catch(error => {{
    console.error(error);
    alert("Failed to load embedded transport data: " + error.message);
}});
</script>
</body>
</html>
"""

    return "\n".join(line.rstrip() for line in html.splitlines()) + "\n"


def main():
    args = parse_args()
    cache_dir = Path(args.cache_dir)
    output_path = Path(args.output)

    transport_pbf = build_cache(
        cache_dir,
        update=args.update,
        rebuild=args.rebuild,
    )

    transport_data = build_transport_data(transport_pbf)
    data = build_index(transport_data, args.grid_size)

    print(f"indexed stops: {len(data['stops'])}")
    print(f"indexed routes: {len(data['routes'])}")
    print(f"grid cells: {len(data['grid'])}")

    html = build_html(
        data,
        args.walk_minutes,
        args.walk_speed,
        args.circle_opacity,
    )

    if not html.isascii():
        for offset, char in enumerate(html):
            if ord(char) > 0x7F:
                raise ValueError(
                    "generated HTML is not ASCII: "
                    f"U+{ord(char):04X} at character offset {offset}"
                )
        raise ValueError("generated HTML is not ASCII")

    output_path.write_bytes(html.encode("ascii"))

    print(f"wrote: {output_path.resolve()}")
    print(f"html size: {output_path.stat().st_size / (1024 * 1024):.1f} MiB")


if __name__ == "__main__":
    main()
