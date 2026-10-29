#!/usr/bin/env python3
"""Moscow transport reachability, CSP-safe static-site generator.

OSM extraction, graph construction and diagnostics run in Python.
The web/ directory contains static HTML/CSS/JS sources copied for deployment.
"""

import argparse
import base64
import gzip
import hashlib
import heapq
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from array import array
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from urllib.parse import quote

try:
    import osmium
except ImportError:
    osmium = None

OsmiumSimpleHandler = osmium.SimpleHandler if osmium is not None else object
PROJECT_VERSION = "0.15.14"


# OSM extraction and normalization

EARTH_RADIUS_M = 6371000.0

# OSMlab provides separate extracts for Moscow city and Moscow Oblast.
# Restore original names to reuse the OSMlab cache without mixing OSM France data.
SOURCE_REGIONS = (
    ("RU-MOW", "https://www.osmlab.ru/regions/RU-MOW.pbf"),
    ("RU-MOS", "https://www.osmlab.ru/regions/RU-MOS.pbf"),
)
MKAD_GEOJSON_URL = (
    "https://raw.githubusercontent.com/karen07/moscow-geo/main/mkad.geojson"
)
MKAD_GEOJSON_CACHE_NAME = "mkad.geojson"
MERGED_SOURCE_NAME = "moscow-regions-osmlab.osm.pbf"
MERGED_MANIFEST_NAME = "moscow-regions-osmlab.sources.json"
TRANSPORT_CACHE_NAME = "moscow-transport-osmlab-v1.osm.pbf"
PEDESTRIAN_CACHE_NAME = "moscow-pedestrian-osmlab-v1.json.gz"
PEDESTRIAN_TEST_CACHE_NAME = "moscow-pedestrian-osmlab-v1-private-test.json.gz"
TRANSFERS_CACHE_NAME = "moscow-walk-transfers-osmlab-v1.json.gz"
TRANSFERS_TEST_CACHE_NAME = "moscow-walk-transfers-osmlab-v1-private-test.json.gz"
SURFACE_MODES = {"bus", "tram", "trolleybus"}
RAIL_ROUTE_TYPES = {"subway", "train", "light_rail", "railway"}
# Explicit intercity services are not ordinary suburban trains.
INTERCITY_SERVICES = {"long_distance", "high_speed", "intercity", "international"}
# Keep commuter stops inside the Moscow catchment in the final index.

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
    "\u043c\u0435\u0442\u0440\u043e\u043f\u043e\u043b\u0438\u0442" "\u0435\u043d"
)
MOSCOW_METROPOLITAN = (
    "\u043c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u0438\u0439 "
    "\u043c\u0435\u0442\u0440\u043e\u043f\u043e\u043b\u0438\u0442"
    "\u0435\u043d"
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


def download_source(url, source_path):
    """Download one regional PBF with retries, HTTP Range resume and atomic replace."""
    source_path.parent.mkdir(parents=True, exist_ok=True)
    part = source_path.with_name(source_path.name + ".part")
    metadata_path = part.with_name(part.name + ".json")
    for attempt in range(1, 6):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            metadata = {}
        # Avoid appending a newer region snapshot to an obsolete partial file.
        validator = metadata.get("validator") if metadata.get("url") == url else None
        if not validator:
            part.unlink(missing_ok=True)
        offset = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": f"moscow-transport-reachability/{PROJECT_VERSION}"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = validator
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status = response.status
                content_range = response.headers.get("Content-Range", "")
                if status == 206:
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise RuntimeError("Unexpected Content-Range from " + url)
                    returned_validator = response.headers.get(
                        "ETag"
                    ) or response.headers.get("Last-Modified")
                    if (
                        validator
                        and returned_validator
                        and returned_validator != validator
                    ):
                        part.unlink(missing_ok=True)
                        metadata_path.unlink(missing_ok=True)
                        raise RuntimeError("Regional PBF changed during HTTP resume")
                    mode = "ab"
                elif status == 200:
                    # A server may ignore Range: restart instead of appending.
                    offset = 0
                    mode = "wb"
                else:
                    raise RuntimeError(f"Unexpected HTTP status {status}: {url}")
                validator = response.headers.get("ETag") or response.headers.get(
                    "Last-Modified"
                )
                if validator:
                    metadata_path.write_text(
                        json.dumps({"url": url, "validator": validator}),
                        encoding="utf-8",
                    )
                else:
                    metadata_path.unlink(missing_ok=True)
                content_length = response.headers.get("Content-Length")
                total = offset + int(content_length) if content_length else None
                downloaded = offset
                next_report = ((downloaded // (64 * 1048576)) + 1) * 64 * 1048576
                with part.open(mode) as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        if downloaded >= next_report:
                            suffix = f" / {total // 1048576} MiB" if total else ""
                            print(
                                f"[{source_path.name}] {downloaded // 1048576}"
                                f" MiB{suffix}",
                                flush=True,
                            )
                            next_report += 64 * 1048576
                if total is not None and downloaded != total:
                    raise OSError(f"Truncated download: {downloaded}/{total} bytes")
            if downloaded == 0:
                raise OSError("Received an empty PBF")
            part.replace(source_path)
            metadata_path.unlink(missing_ok=True)
            print(f"Downloaded: {source_path} ({downloaded // 1048576} MiB)")
            return
        except (OSError, urllib.error.URLError, RuntimeError) as error:
            if isinstance(error, urllib.error.HTTPError):
                if error.code == 416:
                    part.unlink(missing_ok=True)
                    metadata_path.unlink(missing_ok=True)
                # A permanent HTTP error will not be fixed by five retries.
                if error.code in (400, 401, 403, 404, 410):
                    raise RuntimeError(
                        f"OSM source rejected the request (HTTP {error.code}): {url}. "
                        "Check the URL or use a different OSM mirror."
                    ) from error
            if attempt == 5:
                raise RuntimeError(f"Failed to download {url}: {error}") from error
            print(f"Download retry {attempt}/5 for {url}: {error}", flush=True)
            time.sleep(3)


def source_signature(paths):
    """Manifest key invalidating the merged PBF after a region changes."""
    return [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in paths
    ]


def osm_object_key(obj):
    """Sort nodes, ways and relations by type and then original OSM ID."""
    ranks = {"n": 0, "w": 1, "r": 2}
    return ranks[obj.type_str()], obj.id


def merge_osm_pbf_files(sources, destination):
    """Merge sorted regional extracts with bounded memory using pyosmium.

    Keep one current OSM object per source, writing it before advancing that
    source's iterator. This matters because pyosmium returns temporary views.
    If extracts overlap, prefer the higher version of the same OSM object.
    """
    if not hasattr(osmium, "FileProcessor"):
        raise RuntimeError(
            "The OSMlab merge needs pyosmium 4.x (python -m pip install -U osmium)"
        )
    if len(sources) != 2:
        raise ValueError("Expected the two Moscow regional PBF files")

    city = iter(osmium.FileProcessor(str(sources[0])))
    oblast = iter(osmium.FileProcessor(str(sources[1])))
    city_obj = next(city, None)
    oblast_obj = next(oblast, None)
    written = 0
    duplicates = 0
    differing_versions = 0
    previous = None

    with osmium.SimpleWriter(str(destination)) as writer:
        while city_obj is not None or oblast_obj is not None:
            city_key = osm_object_key(city_obj) if city_obj is not None else None
            oblast_key = osm_object_key(oblast_obj) if oblast_obj is not None else None
            if oblast_key is None or (city_key is not None and city_key < oblast_key):
                key = city_key
                chosen = city_obj
                take_city, take_oblast = True, False
            elif city_key is None or oblast_key < city_key:
                key = oblast_key
                chosen = oblast_obj
                take_city, take_oblast = False, True
            else:
                key = city_key
                duplicates += 1
                if city_obj.version != oblast_obj.version:
                    differing_versions += 1
                chosen = (
                    city_obj if city_obj.version >= oblast_obj.version else oblast_obj
                )
                take_city, take_oblast = True, True

            if previous is not None and key <= previous:
                raise RuntimeError(
                    "Unsorted or duplicated OSM objects in source PBF: "
                    f"{key}. Expected strictly increasing type/ID order."
                )
            # The writer must copy the chosen view before either iterator moves.
            writer.add(chosen)
            written += 1
            previous = key
            if take_city:
                city_obj = next(city, None)
            if take_oblast:
                oblast_obj = next(oblast, None)
            if written % 2000000 == 0:
                print(f"[merge] {written:,} OSM objects written", flush=True)

    if differing_versions:
        print(
            f"[merge] WARNING: {differing_versions} overlapping objects "
            "have different versions; confirm the regional snapshot dates",
            flush=True,
        )
    print(
        f"[merge] {written:,} objects, {duplicates:,} shared IDs removed",
        flush=True,
    )
    return written


def merge_regional_sources(cache_dir, sources, rebuild=False):
    """Cache a sorted, ID-deduplicated union of the two OSM regional extracts."""
    target = cache_dir / MERGED_SOURCE_NAME
    manifest = cache_dir / MERGED_MANIFEST_NAME
    signature = source_signature(sources)
    if not rebuild and target.is_file() and manifest.is_file():
        try:
            if json.loads(manifest.read_text(encoding="utf-8")) == signature:
                print(f"Using cached regional union: {target}")
                return target, False
        except (OSError, ValueError):
            pass

    tmp = cache_dir / "moscow-regions-osmlab.tmp.osm.pbf"
    manifest_tmp = manifest.with_name(manifest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    manifest_tmp.unlink(missing_ok=True)
    print("Merging Moscow city + Moscow Oblast using pyosmium...", flush=True)
    try:
        count = merge_osm_pbf_files(sources, tmp)
        if not count or not tmp.is_file() or not tmp.stat().st_size:
            raise RuntimeError("Merged OSM PBF is empty")
        manifest_tmp.write_text(json.dumps(signature, indent=2) + "\n", "utf-8")
        tmp.replace(target)
        manifest_tmp.replace(manifest)
    finally:
        tmp.unlink(missing_ok=True)
        manifest_tmp.unlink(missing_ok=True)
    print(f"Merged regional OSM: {target}")
    return target, True


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
            raise RuntimeError("No supported transport relations found in source PBF")

        tmp_path.replace(transport_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    print(f"selected route masters: {selector.selected_masters}")
    print(f"selected direct routes: {selector.selected_routes}")


def build_cache(cache_dir, update=False, rebuild=False):
    cache_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    for region, url in SOURCE_REGIONS:
        source = cache_dir / f"{region}.osm.pbf"
        if update or not source.is_file():
            print(f"Downloading OSM region {region}: {url}", flush=True)
            download_source(url, source)
        else:
            print(f"Using cached: {source}")
        sources.append(source)

    source, source_changed = merge_regional_sources(cache_dir, sources)
    transport_pbf = cache_dir / TRANSPORT_CACHE_NAME
    if update or rebuild or source_changed or not transport_pbf.is_file():
        print("Selecting all bus/tram/trolleybus networks and urban rail routes...")
        build_transport_extract(source, transport_pbf)
    else:
        print(f"Using cached: {transport_pbf}")
    return transport_pbf, source


# The display area follows the actual MKAD polygon, not a center-point circle.
# Pure Python point classification avoids an extra GIS runtime dependency.


def load_mkad_region(cache_dir, geojson_path, buffer_km):
    if geojson_path is None:
        geojson_path = Path(cache_dir) / MKAD_GEOJSON_CACHE_NAME
        if not geojson_path.is_file():
            print(f"Downloading MKAD boundary: {MKAD_GEOJSON_URL}", flush=True)
            download_source(MKAD_GEOJSON_URL, geojson_path)
    if not geojson_path.is_file():
        raise FileNotFoundError(f"MKAD GeoJSON not found: {geojson_path}")
    source = json.loads(geojson_path.read_text(encoding="utf-8"))
    if source.get("type") == "FeatureCollection":
        geometries = [item.get("geometry", {}) for item in source.get("features", [])]
    elif source.get("type") == "Feature":
        geometries = [source.get("geometry", {})]
    else:
        geometries = [source]
    polygons = [
        geo["coordinates"][0] for geo in geometries if geo.get("type") == "Polygon"
    ]
    if len(polygons) != 1 or len(polygons[0]) < 4:
        raise ValueError("Expected exactly one MKAD Polygon exterior ring")
    return MKADRegion(polygons[0], buffer_km * 1000.0)


class MKADRegion:
    """MKAD interior plus a metric outer buffer, with tile-cached classification."""

    def __init__(self, ring, buffer_m, tile_m=800.0):
        self.origin = (37.6, 55.7)
        self.points = [project(lon, lat, *self.origin) for lon, lat in ring]
        if self.points[0] != self.points[-1]:
            self.points.append(self.points[0])
        self.edges = list(zip(self.points, self.points[1:]))
        self.buffer = buffer_m
        self.tile_m = tile_m
        self.tile_cache = {}
        self.min_x = min(x for x, _ in self.points) - buffer_m
        self.max_x = max(x for x, _ in self.points) + buffer_m
        self.min_y = min(y for _, y in self.points) - buffer_m
        self.max_y = max(y for _, y in self.points) + buffer_m
        # Bucket boundary segments by 2 km squares. A point in the buffer
        # only needs to search nearby squares to determine the distance.
        self.edge_cell_m = 2000.0
        self.edge_cells = defaultdict(list)
        for i, (a, b) in enumerate(self.edges):
            low_x = math.floor(min(a[0], b[0]) / self.edge_cell_m)
            high_x = math.floor(max(a[0], b[0]) / self.edge_cell_m)
            low_y = math.floor(min(a[1], b[1]) / self.edge_cell_m)
            high_y = math.floor(max(a[1], b[1]) / self.edge_cell_m)
            for gx in range(low_x, high_x + 1):
                for gy in range(low_y, high_y + 1):
                    self.edge_cells[gx, gy].append(i)

    def _inside_polygon(self, x, y):
        crossings = False
        for (ax, ay), (bx, by) in self.edges:
            if (ay > y) != (by > y):
                if x < ax + (y - ay) * (bx - ax) / (by - ay):
                    crossings = not crossings
        return crossings

    def _within_distance(self, x, y, radius):
        if radius < 0:
            return False
        gx, gy = math.floor(x / self.edge_cell_m), math.floor(y / self.edge_cell_m)
        span = math.ceil(radius / self.edge_cell_m) + 1
        limit = radius * radius
        seen = set()
        for cx in range(gx - span, gx + span + 1):
            for cy in range(gy - span, gy + span + 1):
                for i in self.edge_cells.get((cx, cy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    a, b = self.edges[i]
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    length_sq = dx * dx + dy * dy
                    if length_sq <= 0:
                        continue
                    t = max(
                        0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / length_sq)
                    )
                    px, py = a[0] + t * dx, a[1] + t * dy
                    if (x - px) ** 2 + (y - py) ** 2 <= limit:
                        return True
        return False

    def _contains_xy(self, x, y):
        if not (self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y):
            return False
        return self._inside_polygon(x, y) or self._within_distance(x, y, self.buffer)

    def contains(self, lon, lat):
        x, y = project(lon, lat, *self.origin)
        if not (self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y):
            return False
        gx = math.floor(x / self.tile_m)
        gy = math.floor(y / self.tile_m)
        cell = (gx, gy)
        state = self.tile_cache.get(cell)
        if state is None:
            cx, cy = (gx + 0.5) * self.tile_m, (gy + 0.5) * self.tile_m
            inside = self._inside_polygon(cx, cy)
            diagonal = self.tile_m * math.sqrt(0.5)
            if inside and not self._within_distance(cx, cy, diagonal):
                state = 1
            elif self._within_distance(cx, cy, max(0.0, self.buffer - diagonal)):
                state = 1
            elif not inside and not self._within_distance(
                cx, cy, self.buffer + diagonal
            ):
                state = -1
            else:
                state = 2
            self.tile_cache[cell] = state
        if state != 2:
            return state == 1
        return self._contains_xy(x, y)


def clip_line_to_region(line, region):
    """Trim real OSM geometry at the region edge; no synthetic through-lines."""
    output = []
    current = []

    def add_segment(a, b):
        nonlocal current
        in_a, in_b = region.contains(*a), region.contains(*b)
        if in_a != in_b:
            lo, hi = a, b
            for _ in range(20):
                mid = ((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0)
                if region.contains(*mid) == in_a:
                    lo = mid
                else:
                    hi = mid
            cross = [round((lo[0] + hi[0]) / 2, 6), round((lo[1] + hi[1]) / 2, 6)]
            if in_a:
                if not current:
                    current = [list(a)]
                current.append(cross)
                if len(current) >= 2:
                    output.append(current)
                current = []
            else:
                current = [cross, list(b)]
        elif in_a:
            if not current:
                current = [list(a)]
            if list(b) != current[-1]:
                current.append(list(b))
        elif current:
            if len(current) >= 2:
                output.append(current)
            current = []

    for a, b in zip(line, line[1:]):
        # Subdivide only very long OSM segments so outside-outside chords
        # crossing the MKAD buffer cannot simply disappear.
        distance = coord_distance_m(a, b)
        pieces = max(1, math.ceil(distance / 500.0))
        prev = a
        for i in range(1, pieces + 1):
            t = i / pieces
            nxt = (
                b
                if i == pieces
                else (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            )
            add_segment(prev, nxt)
            prev = nxt
    if len(current) >= 2:
        output.append(current)
    return output


def clip_geometry_to_region(geometry, region):
    if not geometry:
        return None
    lines = (
        [geometry["coordinates"]]
        if geometry["type"] == "LineString"
        else geometry["coordinates"]
    )
    clipped = [run for line in lines for run in clip_line_to_region(line, region)]
    if not clipped:
        return None
    if len(clipped) == 1:
        return {"type": "LineString", "coordinates": clipped[0]}
    return {"type": "MultiLineString", "coordinates": clipped}


def clip_transport_features(collection, region):
    """Keep real consecutive stop sequences inside the MKAD buffer.

    Splitting each run is essential: deleting an out-of-area stop and joining
    its neighbors would invent a direct transport connection across the gap.
    """
    features = collection.get("features", [])
    stops = {
        f["properties"]["stop_id"]: f
        for f in features
        if f["properties"].get("feature_type") == "stop"
        and region.contains(*f["geometry"]["coordinates"])
    }
    routes = []
    surviving = set()
    keys_by_stop = defaultdict(set)
    split_routes = 0
    geometry_cache = {}
    for feature in features:
        props = feature.get("properties") or {}
        if props.get("feature_type") != "route":
            continue
        members = props.get("stops") or []
        roles = props.get("stop_roles") or []
        runs = []
        current = []
        for pos, stop_id in enumerate(members):
            if stop_id in stops:
                current.append((stop_id, roles[pos] if pos < len(roles) else ""))
            else:
                if len(current) >= 2:
                    runs.append(current)
                current = []
        if len(current) >= 2:
            runs.append(current)
        if not runs:
            continue
        fid = id(feature["geometry"])
        if fid not in geometry_cache:
            geometry_cache[fid] = clip_geometry_to_region(feature["geometry"], region)
        geometry = geometry_cache[fid]
        if geometry is None:
            continue
        split_routes += max(0, len(runs) - 1)
        for run in runs:
            new_props = dict(props)
            new_props["stops"] = [stop_id for stop_id, role in run]
            new_props["stop_roles"] = [role for stop_id, role in run]
            routes.append(
                {"type": "Feature", "properties": new_props, "geometry": geometry}
            )
            surviving.update(new_props["stops"])
            for stop_id in new_props["stops"]:
                keys_by_stop[stop_id].add(new_props["route_key"])
    result_stops = []
    for stop_id, feature in stops.items():
        if stop_id in surviving:
            new_feature = dict(feature)
            new_props = dict(feature["properties"])
            new_props["routes"] = sorted(keys_by_stop[stop_id])
            new_feature["properties"] = new_props
            result_stops.append(new_feature)
    result = dict(collection)
    result["features"] = routes + result_stops
    audit = dict(collection.get("extraction_audit", {}))
    audit["mkad_buffer_m"] = region.buffer
    audit["mkad_kept_stops"] = len(result_stops)
    audit["mkad_kept_directions"] = len(routes)
    audit["mkad_split_directions"] = split_routes
    audit["mkad_dropped_stops"] = sum(
        f["properties"].get("feature_type") == "stop" for f in features
    ) - len(result_stops)
    audit["named_station_candidates"] = [
        candidate
        for candidate in audit.get("named_station_candidates", [])
        if region.contains(candidate["lon"], candidate["lat"])
    ]
    surface_networks = Counter()
    for route in routes:
        props = route["properties"]
        if props.get("mode") in SURFACE_MODES:
            surface_networks[props.get("network") or "(unknown)"] += 1
    audit["surface_networks"] = dict(sorted(surface_networks.items()))
    result["extraction_audit"] = audit
    if surface_networks:
        print("[area] Surface route directions by OSM network:", flush=True)
        for network, count in surface_networks.most_common(12):
            print(f"  {network}: {count}", flush=True)
        if len(surface_networks) > 12:
            print(f"  ... plus {len(surface_networks) - 12} other networks", flush=True)
    print(
        f"[area] MKAD + {region.buffer/1000:.1f} km: "
        f"{len(result_stops)} stops, {len(routes)} directions; "
        f"split {split_routes} directions",
        flush=True,
    )
    return result


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
    missing_stops_by_mode = Counter()
    rail_routes_with_missing_stops = []
    rail_members_merged = 0
    rail_members_orphaned = 0
    rail_routes_outside_area = 0
    mismatched_route_masters = []
    quarantined_way_conflicts = []

    for route in relations.routes:
        relation_id = route["relation_id"]
        parent_key = master_for_relation.get(relation_id)
        mode, master_key, master_conflict = resolve_route_mode_and_master(
            route, parent_key, master_info
        )
        if master_conflict:
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
        route_key = route_identity(mode, ref, relation_id, master_key)

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
            # Leave existing surface route handling unchanged in this release.
            roles = route.get("stop_roles") or []
            matched = [
                (key, roles[i] if i < len(roles) else "")
                for i, key in enumerate(route["stop_members"])
                if key in objects.objects
            ]
            runs = [matched] if len(matched) >= 2 else []
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


# Pedestrian network: OSM node topology, never coordinate-line intersections.
# Walking segments cross a river only if OSM actually connects them via a way.

WALK_HIGHWAYS = {
    "footway",
    "path",
    "cycleway",
    "pedestrian",
    "steps",
    "corridor",
    "elevator",
    "living_street",
    "residential",
    "service",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
    "trunk",
    "track",
    "bridleway",
    "road",
    "tertiary_link",
    "secondary_link",
    "primary_link",
    "trunk_link",
}
WALK_DENIED = {
    "no",
    "private",
    "agricultural",
    "forestry",
    "delivery",
    "use_sidepath",
}
WALK_GRID_M = 100.0
WALK_CACHE_VERSION = 14
# Explicit test-only override of four OSM ways found by the v0.7.6 inspector.
# These ways are tagged access=private. Do not apply the exception globally.
MOSKVORECHYE_PRIVATE_WAYS = frozenset(
    {
        149737448,
        149737453,
        149737460,
        160799180,
    }
)
MOSKVORECHYE_PLATFORM_IDS = frozenset(
    {
        "n1987940555",
        "n1987940556",
    }
)


def allow_station_private_way(way_id, tags, enabled):
    """Explicit, local, experimental station-path access override.

    Private access is not proof of public access. Never bypass foot=no or
    non-footway restrictions, and never generate a synthetic connector.
    """
    if not enabled or way_id not in MOSKVORECHYE_PRIVATE_WAYS:
        return False
    if tags.get("highway") not in {"footway", "path", "steps"}:
        return False
    if (tags.get("access") or "").lower() != "private":
        return False
    if (tags.get("foot") or "").lower() in WALK_DENIED:
        return False
    if tags.get("area") == "yes":
        return False
    return True


MAX_WALK_UI_MINUTES = 15.0
WALK_ZONE_MARGIN_M = 130.0


def is_walkable_way(tags):
    highway = tags.get("highway")
    if highway not in WALK_HIGHWAYS and not (
        highway is None and tags.get("indoor") == "corridor"
    ):
        return False
    if tags.get("area") == "yes" or tags.get("highway") == "construction":
        return False
    if tags.get("motorroad") in ("yes", "1", "true"):
        return False
    # Roads and bridges are walkable unless mapped as exclusively motorized.
    # Never convert a waterway or a railway track into a pedestrian edge.
    if tags.get("waterway") or tags.get("highway") in ("motorway", "motorway_link"):
        return False
    foot = (tags.get("foot") or "").lower()
    access = (tags.get("access") or "").lower()
    if foot in WALK_DENIED:
        return False
    if access in WALK_DENIED and foot not in {"yes", "designated", "permissive"}:
        return False
    if highway in {"trunk", "trunk_link"} and foot not in {
        "yes",
        "designated",
        "permissive",
    }:
        return False
    if tags.get("bridge") in ("yes", "viaduct", "movable"):
        explicit_foot = foot in {"yes", "designated", "permissive"}
        sidewalk = (tags.get("sidewalk") or "").lower()
        if not explicit_foot and sidewalk == "no":
            return False
        if not explicit_foot and highway in {"trunk", "trunk_link"}:
            return False
    # Even sidewalk=separate does not remove a permitted road from this
    # deliberately inclusive coverage graph. Explicit foot=no still wins.
    return True


def is_exterior_walkway(tags):
    """A surface walking edge that can anchor access to the street network."""
    if not is_walkable_way(tags):
        return False
    if tags.get("highway") in {"steps", "elevator", "corridor"}:
        return False
    if tags.get("indoor") not in (None, "no"):
        return False
    if tags.get("tunnel") not in (None, "no"):
        return False
    if tags.get("covered") == "yes":
        return False
    # A bridge is usable as a way but is not a safe point for inferred
    # connectors from a station door onto a different vertical level.
    if tags.get("bridge") not in (None, "no"):
        return False
    return True


def is_linear_station_walkway(tags, references):
    """Trace walkable railway platform geometry using actual OSM way nodes.

    Open ways represent linear platform paths. Closed railway platform ways
    represent a platform surface; its perimeter is a conservative walkable
    approximation. Ways meet only through shared OSM node IDs, never by
    proximity or a visually crossing line. No railway track is walkable.
    """
    if len(references) < 2:
        return False
    railway_platform = tags.get("railway") == "platform"
    public_platform = tags.get("public_transport") == "platform"
    if not (railway_platform or public_platform):
        return False
    foot = (tags.get("foot") or "").lower()
    access = (tags.get("access") or "").lower()
    if foot in WALK_DENIED:
        return False
    if access in WALK_DENIED and foot not in {"yes", "designated", "permissive"}:
        return False
    closed = references[0] == references[-1]
    if closed and len(references) < 4:
        return False
    # A mapped railway platform can also be tagged highway=footway. Keep
    # its walkable perimeter, without treating it as an exterior street.
    return tags.get("highway") in (None, "footway", "pedestrian", "path")


def walking_direction(tags):
    direction = (tags.get("oneway:foot") or "").lower()
    if direction == "-1":
        return 3
    if direction in {"yes", "1", "true"}:
        return 2
    if tags.get("foot:forward") == "no":
        return 3
    if tags.get("foot:backward") == "no":
        return 2
    return 1


class StopZoneIndex:
    """Compact grid for the union of 15-minute circles around stops."""

    def __init__(self, stops, radius, cell_size=400.0):
        self.radius_squared = radius * radius
        self.cell_size = cell_size
        self.whole_cells = set()
        self.boundary = defaultdict(list)
        self.min_x = min(s["x"] for s in stops) - radius
        self.max_x = max(s["x"] for s in stops) + radius
        self.min_y = min(s["y"] for s in stops) - radius
        self.max_y = max(s["y"] for s in stops) + radius
        for stop in stops:
            x, y = stop["x"], stop["y"]
            x0 = math.floor((x - radius) / cell_size)
            x1 = math.floor((x + radius) / cell_size)
            y0 = math.floor((y - radius) / cell_size)
            y1 = math.floor((y + radius) / cell_size)
            for gx in range(x0, x1 + 1):
                for gy in range(y0, y1 + 1):
                    key = (gx, gy)
                    if key in self.whole_cells:
                        continue
                    min_x, max_x = gx * cell_size, (gx + 1) * cell_size
                    min_y, max_y = gy * cell_size, (gy + 1) * cell_size
                    dx = max(min_x - x, 0.0, x - max_x)
                    dy = max(min_y - y, 0.0, y - max_y)
                    if dx * dx + dy * dy > self.radius_squared:
                        continue
                    fx = max(abs(min_x - x), abs(max_x - x))
                    fy = max(abs(min_y - y), abs(max_y - y))
                    if fx * fx + fy * fy <= self.radius_squared:
                        self.whole_cells.add(key)
                        self.boundary.pop(key, None)
                    else:
                        self.boundary[key].append((x, y))

    def contains(self, x, y):
        if x < self.min_x or x > self.max_x:
            return False
        if y < self.min_y or y > self.max_y:
            return False
        key = (math.floor(x / self.cell_size), math.floor(y / self.cell_size))
        if key in self.whole_cells:
            return True
        for sx, sy in self.boundary.get(key, ()):
            dx, dy = sx - x, sy - y
            if dx * dx + dy * dy <= self.radius_squared:
                return True
        return False


class WalkNodeCollector(OsmiumSimpleHandler):
    def __init__(self, stops, origin, limit_m):
        super().__init__()
        self.origin = origin
        self.entrances = StationEntranceCollector(stops, origin)
        self.zones = StopZoneIndex(
            stops,
            limit_m + WALK_ZONE_MARGIN_M,
        )
        self.nodes = {}
        self.node_levels = {}
        self.blocked = set()
        lon, lat = origin
        coslat = math.cos(math.radians(lat))
        degrees_x = 180 / (math.pi * EARTH_RADIUS_M * coslat)
        degrees_y = 180 / (math.pi * EARTH_RADIUS_M)
        self.bounds = (
            lon + self.zones.min_x * degrees_x,
            lat + self.zones.min_y * degrees_y,
            lon + self.zones.max_x * degrees_x,
            lat + self.zones.max_y * degrees_y,
        )

    def node(self, node):
        if not node.location.valid():
            return
        lon, lat = node.location.lon, node.location.lat
        west, south, east, north = self.bounds
        if lon < west or lon > east or lat < south or lat > north:
            return
        x, y = project(lon, lat, *self.origin)
        if not self.zones.contains(x, y):
            return
        self.entrances.node(node)
        self.nodes[node.id] = (round(x, 1), round(y, 1))
        tags = node.tags
        level = tags.get("level")
        layer = tags.get("layer")
        if level is not None or layer is not None:
            self.node_levels[node.id] = (level, layer)
        if tags.get("barrier") and tags.get("access") in WALK_DENIED:
            self.blocked.add(node.id)
        if tags.get("foot") in WALK_DENIED:
            self.blocked.add(node.id)

    def relation(self, relation):
        self.entrances.relation(relation)


class WalkWayCollector(OsmiumSimpleHandler):
    def __init__(
        self,
        nodes,
        blocked,
        allow_private_station_ways=False,
        station_way_ids=None,
        node_levels=None,
    ):
        super().__init__()
        self.node_levels = node_levels or {}
        self.nodes = nodes
        self.blocked = blocked
        self.allow_private_station_ways = allow_private_station_ways
        self.graph_nodes = []
        self.graph_index = {}
        self.station_way_ids = station_way_ids or set()
        self.station_way_nodes = {}
        self.open_platform_endpoints = {}
        self.surface_railway_segments = []
        self.station_way_names = {}
        self.station_way_centers = {}
        self.platform_way_ids = set()
        self.platform_polygons = []
        self.walk_stubs = {}
        self.street_edges = set()
        self.street_nodes = set()
        self.exterior_edges = set()
        self.preferred_exterior_edges = set()
        self.edges = []
        self.preferred_edges = set()
        self.accepted_private_way_ids = set()
        self.ways_count = 0

    def index_for(self, osm_id):
        index = self.graph_index.get(osm_id)
        if index is None:
            index = len(self.graph_nodes)
            self.graph_index[osm_id] = index
            self.graph_nodes.append(self.nodes[osm_id])
        return index

    def way(self, way):
        if way.tags.get("railway") in ("station", "halt", "platform") or (
            way.tags.get("public_transport") in ("station", "platform")
        ):
            if way.tags.get("name"):
                self.station_way_names["w" + str(way.id)] = way.tags.get("name")
        references = [node.ref for node in way.nodes]
        # Physical rail centerlines are used ONLY as barriers for inferred
        # platform-to-footway connectors, never as walkable graph edges.
        rail = way.tags.get("railway")
        if rail in {"rail", "subway", "light_rail", "narrow_gauge"} and (
            way.tags.get("tunnel") in (None, "no")
            and way.tags.get("bridge") in (None, "no")
            and way.tags.get("layer") in (None, "0")
        ):
            for left, right in zip(references, references[1:]):
                if left in self.nodes and right in self.nodes:
                    self.surface_railway_segments.append(
                        (self.nodes[left], self.nodes[right])
                    )
        if way.tags.get("railway") in ("station", "halt", "platform") or (
            way.tags.get("public_transport") in ("station", "platform")
        ):
            known = [self.nodes[ref] for ref in references if ref in self.nodes]
            if known:
                self.station_way_centers["w" + str(way.id)] = (
                    sum(point[0] for point in known) / len(known),
                    sum(point[1] for point in known) / len(known),
                )
        private_station = allow_station_private_way(
            way.id,
            way.tags,
            self.allow_private_station_ways,
        )
        is_street = is_walkable_way(way.tags) or private_station
        tagged_platform = way.tags.get("railway") == "platform" or (
            way.tags.get("public_transport") == "platform"
        )
        is_platform = is_linear_station_walkway(way.tags, references)
        if tagged_platform:
            self.platform_way_ids.add(way.id)
        if not is_street and not is_platform:
            return
        is_outdoor = not private_station and is_exterior_walkway(way.tags)
        is_pedestrian_priority = way.tags.get("highway") in {
            "footway",
            "path",
            "pedestrian",
            "cycleway",
            "living_street",
        }
        # A platform surface is not a public street. The graph can traverse
        # its perimeter, but the coverage overlay must not paint it as a road.
        is_platform_only = tagged_platform
        direction = walking_direction(way.tags)
        if is_platform_only:
            direction += 10
        used = False
        for a_id, b_id in zip(references, references[1:]):
            if a_id not in self.nodes or b_id not in self.nodes:
                continue
            if a_id in self.blocked or b_id in self.blocked:
                continue
            a = self.nodes[a_id]
            b = self.nodes[b_id]
            length = math.hypot(a[0] - b[0], a[1] - b[1])
            if length < 0.1:
                continue
            self.edges.append(
                [
                    self.index_for(a_id),
                    self.index_for(b_id),
                    round(length, 1),
                    direction,
                ]
            )
            edge_index = len(self.edges) - 1
            if private_station:
                self.preferred_edges.add(edge_index)
            if is_street and not is_platform_only:
                self.street_edges.add(edge_index)
                self.street_nodes.update(
                    (self.graph_index[a_id], self.graph_index[b_id])
                )
                if is_outdoor:
                    self.exterior_edges.add(edge_index)
                    if is_pedestrian_priority:
                        self.preferred_exterior_edges.add(edge_index)
            used = True
        if used:
            if (
                tagged_platform
                and len(references) >= 4
                and references[0] == references[-1]
            ):
                if all(ref in self.graph_index for ref in references):
                    ring = [self.graph_index[ref] for ref in references[:-1]]
                    if len(ring) <= 1000 and len(set(ring)) >= 3:
                        self.platform_polygons.append(
                            {
                                "id": way.id,
                                "ring": ring,
                                "level": way.tags.get("level"),
                                "layer": way.tags.get("layer"),
                                "name": way.tags.get("name"),
                                "railway": way.tags.get("railway"),
                            }
                        )
            if is_street and not tagged_platform:
                highway = way.tags.get("highway")
                if (
                    highway
                    in {
                        "footway",
                        "path",
                        "steps",
                        "pedestrian",
                        "corridor",
                    }
                    or way.tags.get("indoor") == "corridor"
                ):
                    for ref in (references[0], references[-1]):
                        if ref in self.graph_index:
                            node_idx = self.graph_index[ref]
                            node_level, node_layer = self.node_levels.get(
                                ref, (None, None)
                            )
                            candidate = (
                                node_level or way.tags.get("level"),
                                node_layer or way.tags.get("layer"),
                                highway or "corridor",
                            )
                            prior = self.walk_stubs.get(node_idx)
                            if prior is None or (
                                candidate[0] is not None and prior[0] is None
                            ):
                                self.walk_stubs[node_idx] = candidate
            if tagged_platform and references[0] != references[-1]:
                ends = []
                for ref in (references[0], references[-1]):
                    if ref in self.graph_index and ref not in self.blocked:
                        ends.append((ref, self.graph_index[ref]))
                if ends:
                    self.open_platform_endpoints[way.id] = {
                        "ends": ends,
                        "level": way.tags.get("level"),
                        "layer": way.tags.get("layer"),
                    }
            if way.id in self.station_way_ids or tagged_platform:
                # A stop_area platform may be absent from the route relation.
                # Retain its true OSM graph vertices for boarding access.
                self.station_way_nodes[way.id] = [
                    self.graph_index[ref]
                    for ref in references
                    if ref in self.graph_index
                ]
            self.ways_count += 1
            if private_station:
                self.accepted_private_way_ids.add(way.id)


def walk_segment_distance(x, y, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 0:
        return 0.0, math.hypot(x - a[0], y - a[1])
    t = min(1.0, max(0.0, ((x - a[0]) * dx + (y - a[1]) * dy) / length_squared))
    dist = math.hypot(x - a[0] - t * dx, y - a[1] - t * dy)
    return t, dist


def build_walk_segment_grid(nodes, edges, cell_size=WALK_GRID_M):
    grid = defaultdict(list)
    for index, (a_idx, b_idx, _length, _direction) in enumerate(edges):
        a, b = nodes[a_idx], nodes[b_idx]
        x0 = math.floor(min(a[0], b[0]) / cell_size)
        x1 = math.floor(max(a[0], b[0]) / cell_size)
        y0 = math.floor(min(a[1], b[1]) / cell_size)
        y1 = math.floor(max(a[1], b[1]) / cell_size)
        # Avoid multiplying very long edges across thousands of cells.
        if (x1 - x0 + 1) * (y1 - y0 + 1) > 10000:
            continue
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                grid[(gx, gy)].append(index)
    return grid


def nearest_walk_segment(
    x, y, nodes, edges, grid, max_snap, preferred_edges=None, allowed_edges=None
):
    cx, cy = math.floor(x / WALK_GRID_M), math.floor(y / WALK_GRID_M)
    radius = math.ceil(max_snap / WALK_GRID_M) + 1
    best = None
    preferred = None
    checked = set()
    for gx in range(cx - radius, cx + radius + 1):
        for gy in range(cy - radius, cy + radius + 1):
            for idx in grid.get((gx, gy), ()):
                if allowed_edges is not None and idx not in allowed_edges:
                    continue
                if idx in checked:
                    continue
                checked.add(idx)
                a, b, _length, _direction = edges[idx]
                t, dist = walk_segment_distance(x, y, nodes[a], nodes[b])
                if dist <= max_snap and (best is None or dist < best[2]):
                    best = (idx, t, dist)
                if (
                    preferred_edges is not None
                    and idx in preferred_edges
                    and dist <= max_snap
                    and (preferred is None or dist < preferred[2])
                ):
                    preferred = (idx, t, dist)
    # Use a station footway only if it is near the otherwise closest edge.
    if preferred and best and preferred[2] <= best[2] + 12.0:
        return preferred
    return best


def segment_intersects(start, end, first, second):
    """True for a real segment intersection, including touching a track."""

    def turn(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    if (
        max(start[0], end[0]) < min(first[0], second[0])
        or max(first[0], second[0]) < min(start[0], end[0])
        or max(start[1], end[1]) < min(first[1], second[1])
        or max(first[1], second[1]) < min(start[1], end[1])
    ):
        return False
    a = turn(start, end, first)
    b = turn(start, end, second)
    c = turn(first, second, start)
    d = turn(first, second, end)
    return a * b <= 0 and c * d <= 0


def railway_barrier_grid(tracks, cell_size=100.0):
    grid = defaultdict(list)
    for idx, (start, end) in enumerate(tracks):
        gx0 = math.floor(min(start[0], end[0]) / cell_size)
        gx1 = math.floor(max(start[0], end[0]) / cell_size)
        gy0 = math.floor(min(start[1], end[1]) / cell_size)
        gy1 = math.floor(max(start[1], end[1]) / cell_size)
        if (gx1 - gx0 + 1) * (gy1 - gy0 + 1) > 400:
            continue
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                grid[(gx, gy)].append(idx)
    return grid


def inferred_link_crosses_rail(start, end, tracks, grid, cell_size=100.0):
    tested = set()
    gx0 = math.floor(min(start[0], end[0]) / cell_size)
    gx1 = math.floor(max(start[0], end[0]) / cell_size)
    gy0 = math.floor(min(start[1], end[1]) / cell_size)
    gy1 = math.floor(max(start[1], end[1]) / cell_size)
    for gx in range(gx0, gx1 + 1):
        for gy in range(gy0, gy1 + 1):
            for idx in grid.get((gx, gy), ()):
                if idx not in tested:
                    tested.add(idx)
                    if segment_intersects(start, end, *tracks[idx]):
                        return True
    return False


def polygon_contains(point, ring):
    """Ray casting, with boundary treated as inside (meter coordinates)."""
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        t, distance = walk_segment_distance(x, y, a, b)
        if distance < 0.12:
            return True
        if (a[1] > y) != (b[1] > y):
            crossing = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < crossing:
                inside = not inside
    return inside


def segment_crosses_polygon(start, end, ring):
    """Reject lines leaving the platform; a straight diagonal is not always valid."""
    if not polygon_contains(start, ring) or not polygon_contains(end, ring):
        return True
    for fraction in (0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875):
        point = (
            start[0] + (end[0] - start[0]) * fraction,
            start[1] + (end[1] - start[1]) * fraction,
        )
        if not polygon_contains(point, ring):
            return True
    return False


def platform_boundary_distance(point, ring):
    if polygon_contains(point, ring):
        return 0.0
    return min(
        walk_segment_distance(point[0], point[1], a, b)[1]
        for a, b in zip(ring, ring[1:] + ring[:1])
    )


def platform_level_compatible(platform, stub):
    """Explicit conflicting levels are forbidden; unknown levels remain unknown."""
    level, layer, _kind = stub
    if (
        platform["level"] is not None
        and level is not None
        and platform["level"] != level
    ):
        return False
    if (
        platform["layer"] is not None
        and layer is not None
        and platform["layer"] != layer
    ):
        return False
    return True


def link_platform_surfaces(
    nodes, edges, platforms, walk_stubs, platform_way_nodes=None, cell_size=100.0
):
    """OTP-style platform entries: connect genuine walking stubs inside areas.

    Do not connect arbitrary roads, disconnected overpasses or different
    explicitly mapped levels. Each added edge is inside the platform polygon
    and keeps its own provenance outside the street coverage drawing.
    """
    active_cells = set()
    for platform in platforms:
        ring = [nodes[index] for index in platform["ring"]]
        if len(ring) < 3:
            continue
        left = min(p[0] for p in ring)
        right = max(p[0] for p in ring)
        bottom = min(p[1] for p in ring)
        top = max(p[1] for p in ring)
        if (right - left) * (top - bottom) > 400000.0:
            continue
        for gx in range(
            math.floor(left / cell_size), math.floor(right / cell_size) + 1
        ):
            for gy in range(
                math.floor(bottom / cell_size), math.floor(top / cell_size) + 1
            ):
                active_cells.add((gx, gy))
    buckets = defaultdict(list)
    for index in walk_stubs:
        x, y = nodes[index]
        cell = (math.floor(x / cell_size), math.floor(y / cell_size))
        if cell in active_cells:
            buckets[cell].append(index)
    counts = Counter()
    connected = set()
    for platform in platforms:
        ring_indices = platform["ring"]
        ring = [nodes[idx] for idx in ring_indices]
        if len(ring) < 3:
            continue
        if platform["railway"] not in ("platform", None):
            continue
        west = min(p[0] for p in ring)
        east = max(p[0] for p in ring)
        south = min(p[1] for p in ring)
        north = max(p[1] for p in ring)
        if (east - west) * (north - south) > 400000.0:
            counts["oversized_polygons"] += 1
            continue
        candidates = set()
        for gx in range(math.floor(west / cell_size), math.floor(east / cell_size) + 1):
            for gy in range(
                math.floor(south / cell_size), math.floor(north / cell_size) + 1
            ):
                candidates.update(buckets.get((gx, gy), ()))
        included = []
        for node in sorted(candidates):
            if node in ring_indices or (platform["id"], node) in connected:
                continue
            point = nodes[node]
            if not (west <= point[0] <= east and south <= point[1] <= north):
                continue
            if not polygon_contains(point, ring):
                continue
            if not platform_level_compatible(platform, walk_stubs[node]):
                counts["different_levels"] += 1
                continue
            # Only connect to visible platform boundary vertices. No shortcut
            # can leave a concave platform polygon or cross a different level.
            visible = []
            for border in ring_indices:
                length = math.hypot(
                    nodes[border][0] - point[0], nodes[border][1] - point[1]
                )
                if length <= 80.0 and not segment_crosses_polygon(
                    point, nodes[border], ring
                ):
                    visible.append((length, border))
            if not visible:
                counts["no_visible_boundary"] += 1
                continue
            # One valid anchor makes the stub part of the actual platform
            # graph; two anchors also allow crossing the interior of a wide
            # area without a detour around the full perimeter.
            for length, border in sorted(visible)[:2]:
                if length > 0.12:
                    edges.append([node, border, round(length, 1), 11])
                    counts["platform_interior_edges"] += 1
            connected.add((platform["id"], node))
            included.append(node)
            if platform_way_nodes is not None:
                platform_way_nodes.setdefault(platform["id"], []).append(node)
            counts["connected_stubs"] += 1
            if (platform["level"] is not None or platform["layer"] is not None) and (
                walk_stubs[node][0] is None and walk_stubs[node][1] is None
            ):
                counts["unknown_stub_level"] += 1
        # Inside a mapped platform surface, two unobstructed entries can be
        # joined directly. This is the area equivalent of a visibility graph.
        # Limit connections for unusually complicated station polygons.
        if len(included) <= 20:
            for left_pos, left in enumerate(included):
                for right in included[left_pos + 1 :]:
                    length = math.hypot(
                        nodes[left][0] - nodes[right][0],
                        nodes[left][1] - nodes[right][1],
                    )
                    if 0.12 < length <= 250.0 and not segment_crosses_polygon(
                        nodes[left], nodes[right], ring
                    ):
                        edges.append([left, right, round(length, 1), 11])
                        counts["platform_visibility_edges"] += 1
    counts["mapped_platform_polygons"] = len(platforms)
    print("[platform] polygon linker:", dict(counts), flush=True)
    return dict(counts)


def match_nearby_platform_polygons(entrances, stops, platforms, nodes):
    """Conservative rail stop_position -> platform matching without stop_area.

    Missing PTv2 platform membership is common. Reject ambiguous parallel
    platforms instead of guessing a transfer across railway tracks.
    """
    cell_size = 100.0
    polygons = []
    grid = defaultdict(set)
    for pos, polygon in enumerate(platforms):
        ring = [nodes[index] for index in polygon["ring"]]
        if len(ring) < 3:
            continue
        polygons.append((polygon, ring))
        index = len(polygons) - 1
        west = min(point[0] for point in ring) - 30.0
        east = max(point[0] for point in ring) + 30.0
        south = min(point[1] for point in ring) - 30.0
        north = max(point[1] for point in ring) + 30.0
        if (east - west) * (north - south) > 450000.0:
            continue
        for gx in range(math.floor(west / cell_size), math.floor(east / cell_size) + 1):
            for gy in range(
                math.floor(south / cell_size), math.floor(north / cell_size) + 1
            ):
                grid[(gx, gy)].add(index)
    counts = Counter()
    for stop in stops:
        modes = set(stop.get("modes", ()))
        if not modes & StationEntranceCollector.RAIL_MODES:
            continue
        if entrances.platform_access.get(stop["id"]):
            continue
        point = stop["x"], stop["y"]
        cell = (math.floor(point[0] / cell_size), math.floor(point[1] / cell_size))
        options = []
        for index in grid.get(cell, ()):
            platform, ring = polygons[index]
            distance = platform_boundary_distance(point, ring)
            limit = 12.0 if "metro" in modes else 25.0
            if distance <= limit:
                options.append((distance, platform["id"]))
        options.sort()
        if not options:
            counts["not_near_platform"] += 1
            continue
        if len(options) > 1 and options[1][0] - options[0][0] < 5.0:
            counts["ambiguous_platforms"] += 1
            continue
        entrances.platform_access[stop["id"]][
            "w" + str(options[0][1])
        ] = "platform_geometry"
        counts["geometry_matched"] += 1
    print("[platform] stop matching:", dict(counts), flush=True)
    return dict(counts)


def station_platform_distance(stop, key, entrances, way_nodes, nodes):
    """Distance to a platform point, line or polygon perimeter (not centroid)."""
    point = (stop["x"], stop["y"])
    if key.startswith("n"):
        target = entrances.station_centers.get(key)
        return math.dist(point, target) if target is not None else float("inf")
    if not key.startswith("w"):
        return float("inf")
    line = way_nodes.get(int(key[1:]), ())
    if not line:
        return float("inf")
    result = min(math.dist(point, nodes[idx]) for idx in line)
    for a, b in zip(line, line[1:]):
        result = min(
            result, walk_segment_distance(point[0], point[1], nodes[a], nodes[b])[1]
        )
    return result


def match_linear_and_point_platforms(entrances, stops, way_nodes, nodes):
    """Match unmapped PTv2 boarding interfaces without crossing parallel tracks.

    Only platform-tagged OSM objects can be candidates. With multiple nearby
    platforms and no explicit stop_area membership, abstain on ambiguity.
    """
    grid = defaultdict(set)
    cell_size = 100.0
    candidates = set(entrances.platform_objects)
    candidates.update("w" + str(item) for item in way_nodes)
    for key in candidates:
        if key.startswith("n"):
            xy = entrances.station_centers.get(key)
            coords = [xy] if xy is not None else []
        else:
            coords = [nodes[index] for index in way_nodes.get(int(key[1:]), ())]
        if not coords:
            continue
        west, east = min(p[0] for p in coords), max(p[0] for p in coords)
        south, north = min(p[1] for p in coords), max(p[1] for p in coords)
        if (east - west) * (north - south) > 400000.0:
            continue
        for gx in range(
            math.floor((west - 30) / cell_size), math.floor((east + 30) / cell_size) + 1
        ):
            for gy in range(
                math.floor((south - 30) / cell_size),
                math.floor((north + 30) / cell_size) + 1,
            ):
                grid[(gx, gy)].add(key)
    counts = Counter()
    for stop in stops:
        modes = set(stop.get("modes", ()))
        if not modes.intersection(StationEntranceCollector.RAIL_MODES):
            continue
        if entrances.platform_access.get(stop["id"]):
            counts["explicit_platform_candidates"] += 1
            continue
        cell = (math.floor(stop["x"] / cell_size), math.floor(stop["y"] / cell_size))
        options = []
        limit = 12.0 if "metro" in modes else 25.0
        name = canonical_station_name(stop.get("name"))
        for key in grid.get(cell, ()):
            platform_name = entrances.station_names.get(
                key
            ) or entrances._way_platform_names.get(key)
            if platform_name and canonical_station_name(platform_name) != name:
                continue
            distance = station_platform_distance(stop, key, entrances, way_nodes, nodes)
            if distance <= limit:
                options.append((distance, key))
        options.sort()
        if not options:
            counts["no_platform_candidate"] += 1
            continue
        if len(options) > 1 and options[1][0] - options[0][0] < 6.0:
            counts["ambiguous_platform_candidate"] += 1
            continue
        entrances.platform_access[stop["id"]][options[0][1]] = "platform_geometry"
        counts["matched_point_or_linear_platform"] += 1
    print("[platform] point/line matching:", dict(counts), flush=True)
    return dict(counts)


def choose_boarding_platforms(entrances, stops, way_nodes, nodes):
    """Stop_area is station membership, not permission to board ANY platform.

    Select a unique geometrically nearest platform. If there are equally
    plausible platforms, abstain instead of manufacturing a rail crossing.
    """
    counts = Counter()
    for stop in stops:
        access = entrances.platform_access.get(stop["id"])
        if not access:
            continue
        scored = sorted(
            (station_platform_distance(stop, key, entrances, way_nodes, nodes), key)
            for key in access
        )
        scored = [candidate for candidate in scored if math.isfinite(candidate[0])]
        if not scored:
            access.clear()
            counts["unlocated_platforms"] += 1
            continue
        # Even an explicitly mapped stop_area is not a pass for a distant
        # platform. Position-to-platform horizontal separation must be sane.
        limit = (
            100.0
            if len(scored) == 1
            else (65.0 if "metro" in stop.get("modes", ()) else 45.0)
        )
        if scored[0][0] > limit:
            access.clear()
            counts["too_distant_platforms"] += 1
            continue
        if len(scored) > 1 and scored[1][0] - scored[0][0] < 6.0:
            access.clear()
            counts["ambiguous_boarding_platforms"] += 1
            continue
        key = scored[0][1]
        role = access[key]
        access.clear()
        access[key] = role
        counts["selected_unique_platform"] += 1
    print("[platform] boarding selection:", dict(counts), flush=True)
    return dict(counts)


def inferred_platform_point_stops(entrances, ways, parents, trusted_roots):
    """Use mapped footways for point platforms and ends of open platform ways.

    Never snap to motor roads, bridges, tunnels, or unrelated railway tracks.
    Avoid inferred connections across explicitly elevated/depressed levels.
    """

    def surface_level(level, layer):
        return level in (None, "0", "0.0") and layer in (None, "0", "0.0")

    # Only interfaces identified with at least one actual rail route are used.
    referenced = {
        key for accesses in entrances.platform_access.values() for key in accesses
    }
    result = []
    for key in sorted(referenced):
        if key.startswith("n"):
            level, layer = entrances.platform_point_levels.get(key, (None, None))
            if not surface_level(level, layer):
                continue
            coords = entrances.station_centers.get(key)
            if coords is None:
                continue
            osm_id = int(key[1:])
            graph_node = ways.graph_index.get(osm_id)
            if graph_node is not None and node_in_exterior_component(
                graph_node, parents, trusted_roots
            ):
                continue
            result.append(
                {
                    "id": key,
                    "entrance_id": osm_id,
                    "platform_key": key,
                    "x": coords[0],
                    "y": coords[1],
                    "snap_limit": 10.0,
                    "facility_only_footways": True,
                    "modes": [],
                }
            )
        elif key.startswith("w"):
            item = ways.open_platform_endpoints.get(int(key[1:]))
            if item is None or not surface_level(item["level"], item["layer"]):
                continue
            for osm_id, graph_node in item["ends"]:
                if node_in_exterior_component(graph_node, parents, trusted_roots):
                    continue
                x, y = ways.graph_nodes[graph_node]
                result.append(
                    {
                        "id": "n" + str(osm_id),
                        "entrance_id": osm_id,
                        "platform_key": key,
                        "x": x,
                        "y": y,
                        "snap_limit": 10.0,
                        "facility_only_footways": True,
                        "modes": [],
                    }
                )
    return result


def exterior_component_edges(count, edges, exterior_edges, preferred_edges):
    """Find public street components without treating metro stairs as streets.

    A small indoor/entrance island cannot be an anchor even when one of its
    segments is tagged highway=footway. Parent arrays use 32-bit entries to
    avoid per-vertex Python objects on the Moscow-wide graph.
    """
    parent = array("I", range(count))
    sizes = array("I", [1]) * count

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for a, b, _length, _direction in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            if sizes[ra] < sizes[rb]:
                ra, rb = rb, ra
            parent[rb] = ra
            sizes[ra] += sizes[rb]
    anchors = Counter()
    for index in exterior_edges:
        a = edges[index][0]
        anchors[find(a)] += 1
    # One real OSM outdoor edge is sufficient evidence of a mapped outdoor
    # approach. Do not reject short sidewalks at elevated/terminal stations.
    # Larger components are required only when *inferring* a missing link.
    mapped_roots = set(anchors)
    inferred_roots = {
        root
        for root, num in anchors.items()
        if num >= 4 and (sizes[root] >= 32 or num >= 12)
    }
    inferred_edges = {
        index for index in exterior_edges if find(edges[index][0]) in inferred_roots
    }
    preferred = inferred_edges & preferred_edges
    print(
        "[walk] mapped outdoor components:",
        len(mapped_roots),
        "inferred-link candidate components:",
        len(inferred_roots),
        "candidate edges:",
        len(inferred_edges),
    )
    return parent, mapped_roots, inferred_roots, inferred_edges, preferred


def node_in_exterior_component(node, parent, trusted):
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node in trusted


# A route stop is a boarding position, not necessarily a street entrance.
# Only same-stop_area membership authorizes a virtual indoor connection.
# These links are routing estimates, not mapped pedestrian geometries.


def canonical_station_name(name):
    """Conservative normalization, never fuzzy-match station names."""
    name = (name or "").casefold().replace("\u0451", "\u0435").strip()
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    station = "\u0441\u0442\u0430\u043d\u0446\u0438\u044f"
    metro = "\u043c\u0435\u0442\u0440\u043e"
    platform = "\u043f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u0430"
    stopping_point = (
        "\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043e\u0447\u043d\u044b\u0439 "
        "\u043f\u0443\u043d\u043a\u0442"
    )
    name = re.sub(rf"^(?:{station}\s+)?(?:{metro}\s+)", "", name)
    name = re.sub(rf"^(?:{station}|{platform}|{stopping_point})\s+", "", name)
    return " ".join(name.split())


class StationEntranceCollector(OsmiumSimpleHandler):
    """Retain complete stop_area membership, including non-route station IDs.

    A route commonly refers to stop_position on the rail, while stop_area
    contains a separately mapped railway=station or platform object. The
    original exact-ID-only matching silently dropped such stations.
    """

    RAIL_MODES = frozenset(("metro", "mcc", "mcd", "train"))

    def __init__(self, stops, origin):
        super().__init__()
        self.origin = origin
        self.rail_stops = {
            stop["id"] for stop in stops if set(stop.get("modes", ())) & self.RAIL_MODES
        }
        self.entrances = {}
        self.station_names = {}
        self.station_centers = {}
        self.platform_objects = set()
        self.platform_point_levels = {}
        self._way_platform_names = {}
        self.facility_nodes = {}
        self.facility_access = defaultdict(dict)
        self.areas = []
        self.area_groups = []
        self.area_route_stops = defaultdict(set)
        self.access = defaultdict(dict)
        self.platform_access = defaultdict(dict)
        self.matched_areas = 0
        self.resolved_exact = 0
        self.resolved_named = 0

    def node(self, node):
        if not node.location.valid():
            return
        tags = node.tags
        if tags.get("railway") in ("station", "halt", "platform") or (
            tags.get("public_transport") in ("station", "platform")
        ):
            key = "n" + str(node.id)
            if tags.get("railway") == "platform" or (
                tags.get("public_transport") == "platform"
            ):
                self.platform_objects.add(key)
                self.platform_point_levels[key] = (tags.get("level"), tags.get("layer"))
            x, y = project(node.location.lon, node.location.lat, *self.origin)
            self.station_centers[key] = (round(x, 1), round(y, 1))
            if tags.get("name"):
                self.station_names[key] = tags.get("name")
            if tags.get("railway") in ("station", "halt"):
                self.facility_nodes[node.id] = {
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "name": tags.get("name"),
                    "layer": tags.get("layer"),
                    "level": tags.get("level"),
                }
        is_entrance = (
            tags.get("railway") in ("subway_entrance", "train_station_entrance")
            or tags.get("entrance") in ("yes", "main", "entrance", "exit")
            or tags.get("public_transport") == "entrance"
            or tags.get("highway") == "elevator"
        )
        if not is_entrance:
            return
        if (tags.get("access") or "").lower() in WALK_DENIED:
            return
        if (tags.get("foot") or "").lower() in WALK_DENIED:
            return
        x, y = project(node.location.lon, node.location.lat, *self.origin)
        role = (tags.get("entrance") or "").lower()
        direction = 12 if role == "exit" else 13 if role == "entrance" else 11
        self.entrances[node.id] = (round(x, 1), round(y, 1), direction)

    def relation(self, relation):
        kind = relation.tags.get("public_transport")
        if kind not in ("stop_area", "stop_area_group"):
            return
        members = [
            (member.type + str(member.ref), member.role or "")
            for member in relation.members
        ]
        if kind == "stop_area_group":
            self.area_groups.append((relation.id, members))
        else:
            self.areas.append((relation.id, relation.tags.get("name"), members))

    def resolve(self, stops, way_names=None, way_centers=None, platform_way_ids=None):
        """Match same-area stops by ID, or by station name AND proximity.

        Only a real stop_area relation grants access. Name-only matching
        never links entrances that are not already members of the relation.
        """
        way_names = way_names or {}
        way_centers = way_centers or {}
        platform_keys = self.platform_objects | {
            "w" + str(ident) for ident in (platform_way_ids or ())
        }
        stop_by_id = {
            stop["id"]: stop for stop in stops if stop["id"] in self.rail_stops
        }
        by_name = defaultdict(list)
        for stop in stop_by_id.values():
            label = canonical_station_name(stop.get("name"))
            if label:
                by_name[label].append(stop)
        areas_seen = 0
        for relation_id, area_name, members in self.areas:
            entrances = {}
            for key, role in members:
                if not key.startswith("n"):
                    continue
                entry = self.entrances.get(int(key[1:]))
                if entry is None:
                    continue
                direction = entry[2]
                if role == "entry_only":
                    direction = 13
                elif role == "exit_only":
                    direction = 12
                entrances[int(key[1:])] = (entry[0], entry[1], direction)
            # A stop_area can identify a transfer even if no station
            # entrances are mapped. Do not discard its membership.
            locations = [entry[:2] for entry in entrances.values()]
            for key, _role in members:
                center = self.station_centers.get(key) or way_centers.get(key)
                if center is not None:
                    locations.append(center)
            labels = set()
            name = canonical_station_name(area_name)
            if name:
                labels.add(name)
            for key, _role in members:
                member_name = self.station_names.get(key) or way_names.get(key)
                if member_name:
                    labels.add(canonical_station_name(member_name))
            exact = {key for key, _role in members if key in stop_by_id}
            matched = set(exact)
            for label in labels:
                for stop in by_name.get(label, ()):
                    if stop["id"] in matched:
                        continue
                    # A stop_area is station-specific, but names can repeat.
                    # Match named platform/station geometry or an entrance,
                    # never an arbitrary equal name across the city.
                    limit = 400.0 if "metro" in stop.get("modes", ()) else 650.0
                    near = any(
                        math.hypot(stop["x"] - x, stop["y"] - y) <= limit
                        for x, y in locations
                    )
                    if near:
                        matched.add(stop["id"])
            if not matched:
                continue
            areas_seen += 1
            self.area_route_stops[relation_id].update(matched)
            self.resolved_exact += len(exact)
            self.resolved_named += len(matched - exact)
            # PTv2 often maps train platforms but no named building entrance.
            # These are candidate boarding nodes, NOT inferred street links.
            platforms = [
                (key, role)
                for key, role in members
                if key[:1] in ("n", "w")
                and (role.startswith("platform") or key in platform_keys)
            ]
            for stop_id in matched:
                stop = stop_by_id[stop_id]
                limit = 400.0 if "metro" in stop.get("modes", ()) else 650.0
                for key, role in platforms:
                    center = self.station_centers.get(key) or way_centers.get(key)
                    if center is None:
                        continue
                    distance = math.hypot(stop["x"] - center[0], stop["y"] - center[1])
                    if distance > limit:
                        continue
                    self.platform_access[stop_id][key] = role
                for entrance_id, entrance in entrances.items():
                    # Avoid an exit for a different platform at far end of
                    # an interchange when the stop was name-matched.
                    if stop_id not in exact:
                        stop = stop_by_id[stop_id]
                        limit = 400.0 if "metro" in stop.get("modes", ()) else 650.0
                        if (
                            math.hypot(stop["x"] - entrance[0], stop["y"] - entrance[1])
                            > limit
                        ):
                            continue
                    self.access[stop_id][entrance_id] = entrance
        self.matched_areas = areas_seen
        print(
            "[station] stop_area matches:",
            areas_seen,
            "exact:",
            self.resolved_exact,
            "name+distance:",
            self.resolved_named,
        )


def match_station_facility_anchors(entrances, stops):
    """Find a mapped railway station point for stops without platform areas.

    This establishes station identity, NOT a verified pedestrian passage.
    Final access must be anchored to a permitted mapped outdoor footway,
    and the resulting connection is reported as an estimated boarding link.
    """
    by_name = defaultdict(list)
    for osm_id, facility in entrances.facility_nodes.items():
        name = canonical_station_name(facility["name"])
        if not name:
            continue
        # An underground railway marker is not a surface door. Matching
        # it to an unrelated sidewalk would bypass levels or rail tracks.
        if str(facility["layer"] or "0") not in ("0", "0.0"):
            continue
        if str(facility["level"] or "0") not in ("0", "0.0"):
            continue
        by_name[name].append((osm_id, facility))
    counts = Counter()
    for stop in stops:
        modes = set(stop.get("modes", ()))
        if not modes.intersection({"mcc", "mcd", "train"}):
            continue
        name = canonical_station_name(stop.get("name"))
        if not name:
            continue
        choices = []
        for osm_id, facility in by_name.get(name, ()):
            distance = math.hypot(stop["x"] - facility["x"], stop["y"] - facility["y"])
            if distance <= 30.0:
                choices.append((distance, osm_id))
        choices.sort()
        if not choices:
            counts["no_named_facility_within_30m"] += 1
            continue
        if len(choices) > 1 and choices[1][0] - choices[0][0] < 5.0:
            counts["ambiguous_facility"] += 1
            continue
        # A nearby point is only an identity candidate. Physical street
        # attachment is checked independently after actual graph extraction.
        entrances.facility_access[stop["id"]][choices[0][1]] = choices[0][0]
        counts["matched_facility_stops"] += 1
    print("[facility] station point matching:", dict(counts), flush=True)
    return dict(counts)


def add_station_facility_fallback(graph, stops, entrances, facility_stops):
    """Use a named station point only when its outdoor footway is reachable.

    This is an *estimated* station-to-platform boarding cost. The same
    facility cannot make an inaccessible motorway, private path or railway
    track into a pedestrian edge. A stop with verified platform or entrance
    access is never replaced by this fallback.
    """
    anchors = {
        item["facility_id"]: item.get("walk_node", -1) for item in facility_stops
    }
    counts = Counter()
    for stop in stops:
        if stop.get("walk_node", -1) >= 0:
            continue
        facility_ids = entrances.facility_access.get(stop["id"], {})
        usable = [
            facility_id
            for facility_id in facility_ids
            if anchors.get(facility_id, -1) >= 0
        ]
        if len(usable) != 1:
            if facility_ids:
                counts["matched_but_no_walkable_facility"] += 1
            continue
        facility_id = usable[0]
        anchor = anchors[facility_id]
        node = len(graph["nodes"])
        graph["nodes"].append([stop["x"], stop["y"]])
        direct_distance = math.hypot(
            stop["x"] - graph["nodes"][anchor][0],
            stop["y"] - graph["nodes"][anchor][1],
        )
        # Conservative estimate for boarding, circulation and uncertainty.
        # Never treat a station-point association as a zero-cost transfer.
        cost = round(max(180.0, direct_distance + 120.0), 1)
        graph["edges"].append([node, anchor, cost, 11])
        stop["walk_node"] = node
        stop["station_access_kind"] = "station_facility_estimated"
        counts["estimated_station_facility_links"] += 1
    print("[facility] station boarding fallback:", dict(counts), flush=True)
    return dict(counts)


def station_platform_graph_node(
    key,
    stop,
    nodes,
    osm_node_index,
    osm_way_index,
    parents,
    exterior_roots,
    snapped_platform_nodes=None,
):
    """Resolve a PTv2 platform to a mapped, street-reachable walking node.

    The OSM stop_area authorizes identifying the boarding platform, but does
    not authorize a pedestrian shortcut. The platform must already share a
    connected walking component with a mapped outdoor OSM edge.
    """
    snapped_platform_nodes = snapped_platform_nodes or {}
    inferred = snapped_platform_nodes.get(key, ())
    if inferred:
        return min(
            inferred,
            key=lambda node: math.hypot(
                nodes[node][0] - stop["x"], nodes[node][1] - stop["y"]
            ),
        )
    if key.startswith("n"):
        node = osm_node_index.get(int(key[1:]))
        candidates = [node] if node is not None else []
    elif key.startswith("w"):
        candidates = osm_way_index.get(int(key[1:]), ())
    else:
        candidates = ()
    valid = [
        node
        for node in candidates
        if 0 <= node < len(parents)
        and node_in_exterior_component(node, parents, exterior_roots)
    ]
    if not valid:
        return None
    return min(
        valid,
        key=lambda node: math.hypot(
            nodes[node][0] - stop["x"], nodes[node][1] - stop["y"]
        ),
    )


def add_station_access_links(
    graph,
    stops,
    entrances,
    entrance_stops,
    osm_node_index=None,
    osm_way_index=None,
    exterior_parents=None,
    exterior_roots=None,
    snapped_platform_nodes=None,
):
    """Build boarding links from mapped entrances OR reachable OSM platforms.

    A stop_position on train tracks is not itself a pedestrian street node.
    stop_area membership identifies permitted boarding interfaces, while
    shared OSM walking-component membership validates platform street access.
    Every boarding-to-platform edge is explicitly an estimated station cost.
    """
    nodes = graph["nodes"]
    edges = graph["edges"]
    osm_node_index = osm_node_index or {}
    osm_way_index = osm_way_index or {}
    entrance_nodes = {item["entrance_id"]: item["walk_node"] for item in entrance_stops}
    attached = 0
    connected_entrances = 0
    connected_platforms = 0
    missing = 0
    still_unattached = 0
    for stop in stops:
        available = {}
        for entrance_id, (x, y, direction) in entrances.access.get(
            stop["id"], {}
        ).items():
            node = entrance_nodes.get(entrance_id, -1)
            if node >= 0:
                available[node] = (x, y, direction, "entrance")
        for key, role in entrances.platform_access.get(stop["id"], {}).items():
            if exterior_parents is None or exterior_roots is None:
                continue
            node = station_platform_graph_node(
                key,
                stop,
                nodes,
                osm_node_index,
                osm_way_index,
                exterior_parents,
                exterior_roots,
                snapped_platform_nodes=snapped_platform_nodes,
            )
            if node is None or node in available:
                continue
            direction = (
                13 if "entry_only" in role else (12 if "exit_only" in role else 11)
            )
            x, y = nodes[node]
            source = "platform_geometry" if role == "platform_geometry" else "platform"
            available[node] = (x, y, direction, source)
        if not available:
            if entrances.access.get(stop["id"]) or entrances.platform_access.get(
                stop["id"]
            ):
                missing += 1
            if stop.get("walk_node", -1) < 0:
                still_unattached += 1
            continue
        hub = len(nodes)
        nodes.append([stop["x"], stop["y"]])
        stop["walk_node"] = hub
        source_types = {item[3] for item in available.values()}
        stop["station_access_kind"] = (
            "stop_area_entrance+platform"
            if "entrance" in source_types and len(source_types) > 1
            else (
                "stop_area_entrance"
                if "entrance" in source_types
                else (
                    "platform_geometry_inferred"
                    if "platform_geometry" in source_types
                    else "stop_area_platform"
                )
            )
        )
        for node, (x, y, direction, source) in available.items():
            straight = math.hypot(x - stop["x"], y - stop["y"])
            length = round(max(100.0, min(650.0, straight + 50.0)), 1)
            edges.append([hub, node, length, direction])
            if source == "entrance":
                connected_entrances += 1
            else:
                connected_platforms += 1
        attached += 1
    print(
        "[station] stop_area:",
        entrances.matched_areas,
        "stations with entrance/platform access:",
        attached,
        "entrance links:",
        connected_entrances,
        "reachable platform links:",
        connected_platforms,
        "mapped members without usable street access:",
        missing,
        "rail stops still unattached:",
        still_unattached,
    )
    return attached


def station_interchange_hints(entrances, stops):
    """Transfer-only estimates authorized by an OSM stop_area_group.

    A group is evidence of an interchange, not a mapped walkway. Hints never
    enter the on-map pedestrian graph; a conservative interchange cost is
    charged and only stops with a real attached walking node are eligible.
    """
    index_by_id = {stop["id"]: idx for idx, stop in enumerate(stops)}
    hints = {}
    for relation_id, members in entrances.area_groups:
        area_ids = [
            int(key[1:])
            for key, _role in members
            if key.startswith("r") and int(key[1:]) in entrances.area_route_stops
        ]
        if len(area_ids) < 2:
            continue
        for first_pos, left_id in enumerate(area_ids):
            for right_id in area_ids[first_pos + 1 :]:
                for a_id in entrances.area_route_stops[left_id]:
                    for b_id in entrances.area_route_stops[right_id]:
                        a, b = index_by_id.get(a_id), index_by_id.get(b_id)
                        if a is None or b is None or a == b:
                            continue
                        # Interchange transfer is independent of street
                        # entrance accessibility. A platform can have no
                        # street walk_node and still connect to another
                        # platform through a mapped stop_area_group.
                        distance = math.hypot(
                            stops[a]["x"] - stops[b]["x"],
                            stops[a]["y"] - stops[b]["y"],
                        )
                        # Avoid linking separate terminals that merely share
                        # a regional station/group name.
                        if distance > 350.0:
                            continue
                        penalty = round(max(220.0, distance + 120.0), 2)
                        for source, target in ((a, b), (b, a)):
                            key = source, target
                            old = hints.get(key)
                            if old is None or penalty < old[0]:
                                hints[key] = (penalty, relation_id)
    result = [
        [a, b, length, relation_id]
        for (a, b), (length, relation_id) in sorted(hints.items())
    ]
    print(
        "[station] stop_area_group transfer hints:",
        len(result),
        "from groups:",
        len(entrances.area_groups),
        "(estimated; never street shortcuts)",
    )
    return result


def attach_stops_to_walk_graph(
    nodes,
    edges,
    stops,
    preferred_edges=None,
    osm_node_index=None,
    osm_way_index=None,
    street_edges=None,
    street_nodes=None,
    station_access_ids=None,
    exterior_parents=None,
    exterior_roots=None,
    trusted_exterior_roots=None,
    preferred_exterior_edges=None,
    railway_segments=None,
):
    """Attach stops to mapped ways and station entrances to trusted streets."""
    grid = build_walk_segment_grid(nodes, edges)
    rail_grid = railway_barrier_grid(railway_segments) if railway_segments else {}
    attachments = defaultdict(list)
    stop_nodes = [-1] * len(stops)
    missed = Counter()
    direct = 0
    direct_ways = 0
    inferred_exits = 0
    exact_outdoor_fallback = {}
    for idx, stop in enumerate(stops):
        # A shared OSM node ID is real topology. Snapping by coordinates
        # instead might choose the wrong side of a railway or an overpass.
        stop_id = stop["id"]
        is_entrance = "entrance_id" in stop
        only_footways = stop.get("facility_only_footways", False)
        # Retain real direct OSM walking attachments even if stop_area names
        # entrances. An unavailable entrance must not erase a valid platform.
        if osm_node_index is not None and stop_id.startswith("n"):
            walk_node = osm_node_index.get(int(stop_id[1:]))
            if walk_node is not None:
                if not is_entrance or exterior_parents is None:
                    stop_nodes[idx] = walk_node
                    direct += 1
                    continue
                # A short mapped outdoor sidewalk is real, but may end
                # abruptly before the rest of the walking network. Retain
                # its exact node and *also* try a conservative street link.
                is_outdoor = node_in_exterior_component(
                    walk_node, exterior_parents, exterior_roots
                )
                if is_outdoor:
                    exact_outdoor_fallback[idx] = walk_node
                if is_outdoor and (
                    trusted_exterior_roots is None
                    or node_in_exterior_component(
                        walk_node, exterior_parents, trusted_exterior_roots
                    )
                ):
                    stop_nodes[idx] = walk_node
                    direct += 1
                    continue
        if osm_way_index is not None and stop_id.startswith("w"):
            vertices = osm_way_index.get(int(stop_id[1:]), ())
            if vertices:
                stop_nodes[idx] = min(
                    vertices,
                    key=lambda node: math.hypot(
                        nodes[node][0] - stop["x"],
                        nodes[node][1] - stop["y"],
                    ),
                )
                direct_ways += 1
                continue
        # Railway track points are not street points. Without a confirmed
        # entrance or a real shared OSM way/node, geometric proximity alone
        # does not authorize pedestrian access across tracks or levels.
        modes = stop.get("modes", ())
        if not is_entrance and set(modes) & StationEntranceCollector.RAIL_MODES:
            missed["rail_stop_without_mapped_access"] += 1
            continue
        snap_limit = stop.get("snap_limit")
        if snap_limit is None:
            snap_limit = (
                100.0
                if any(mode in ("metro", "mcc", "mcd") for mode in modes)
                else 45.0
            )
        rail_preference = (
            preferred_edges if stop["id"] in MOSKVORECHYE_PLATFORM_IDS else None
        )
        if only_footways:
            # A railway=station point can be on the rails. A short *mapped*
            # public outdoor footway is essential for this estimated fallback.
            # Never snap this semantic station marker to a road carriageway.
            nearest = nearest_walk_segment(
                stop["x"],
                stop["y"],
                nodes,
                edges,
                grid,
                snap_limit,
                allowed_edges=preferred_exterior_edges or set(),
            )
        elif is_entrance and preferred_exterior_edges:
            # A station door is not a license to cross a four-lane road.
            # Prefer a nearby mapped sidewalk; use a road axis only when
            # the door is very close and no suitable footway is mapped.
            nearest = nearest_walk_segment(
                stop["x"],
                stop["y"],
                nodes,
                edges,
                grid,
                snap_limit,
                allowed_edges=preferred_exterior_edges,
            )
            if nearest is None:
                nearest = nearest_walk_segment(
                    stop["x"],
                    stop["y"],
                    nodes,
                    edges,
                    grid,
                    min(snap_limit, 18.0),
                    allowed_edges=street_edges,
                )
        else:
            nearest = nearest_walk_segment(
                stop["x"],
                stop["y"],
                nodes,
                edges,
                grid,
                snap_limit,
                preferred_edges=rail_preference,
                allowed_edges=street_edges if is_entrance else None,
            )
        if nearest is None:
            if idx in exact_outdoor_fallback:
                stop_nodes[idx] = exact_outdoor_fallback[idx]
                direct += 1
                continue
            missed["no_pedestrian_path_near_stop"] += 1
            continue
        edge_idx, t, lateral = nearest
        if stop.get("platform_key") and rail_grid:
            a_idx, b_idx = edges[edge_idx][:2]
            start, end = nodes[a_idx], nodes[b_idx]
            projected = (
                start[0] + t * (end[0] - start[0]),
                start[1] + t * (end[1] - start[1]),
            )
            if inferred_link_crosses_rail(
                (stop["x"], stop["y"]), projected, railway_segments, rail_grid
            ):
                missed["platform_connector_crosses_surface_rail"] += 1
                continue
        attachments[edge_idx].append((t, idx, lateral))
    del grid
    result_edges = []
    for edge_idx, (a, b, length, direction) in enumerate(edges):
        matches = sorted(attachments.get(edge_idx, ()))
        if not matches:
            result_edges.append([a, b, length, direction])
            continue
        sequence = [(0.0, a)]
        for t, stop_index, lateral in matches:
            if t <= 0.000001:
                walk_node = a
            elif t >= 0.999999:
                walk_node = b
            elif sequence[-1][0] == t:
                walk_node = sequence[-1][1]
            else:
                first, second = nodes[a], nodes[b]
                nodes.append(
                    [
                        round(first[0] + (second[0] - first[0]) * t, 1),
                        round(first[1] + (second[1] - first[1]) * t, 1),
                    ]
                )
                walk_node = len(nodes) - 1
                sequence.append((t, walk_node))
            stop = stops[stop_index]
            # Preserve a real OSM station entrance node and its existing
            # internal stair connections when attaching to a surface road.
            existing = None
            if "entrance_id" in stop and osm_node_index is not None:
                existing = osm_node_index.get(stop["entrance_id"])
            if existing is not None and existing != walk_node:
                stop_nodes[stop_index] = existing
                result_edges.append(
                    [walk_node, existing, max(0.1, round(lateral, 1)), 5]
                )
                inferred_exits += 1
            elif lateral < 0.15:
                stop_nodes[stop_index] = walk_node
            else:
                nodes.append([stop["x"], stop["y"]])
                stop_node = len(nodes) - 1
                stop_nodes[stop_index] = stop_node
                # Local stop-to-street connector, never a stop-to-stop shortcut.
                result_edges.append([walk_node, stop_node, round(lateral, 1), 5])
                if "entrance_id" in stop:
                    inferred_exits += 1
        sequence.append((1.0, b))
        for (t0, n0), (t1, n1) in zip(sequence, sequence[1:]):
            if n0 != n1 and t1 - t0 > 0.0000001:
                result_edges.append([n0, n1, round(length * (t1 - t0), 1), direction])
    for index, node in enumerate(stop_nodes):
        stops[index]["walk_node"] = node
    print(f"pedestrian stops attached by exact OSM node: {direct}")
    print(f"pedestrian stops attached by platform way: {direct_ways}")
    print(f"[station] inferred entrance-to-street links (<=35m): {inferred_exits}")
    return {"nodes": nodes, "edges": result_edges}, dict(missed)


def build_pedestrian_graph(
    source_path, stops, origin, limit_m, allow_private_station_ways=False
):
    if osmium is None:
        raise RuntimeError("Install osmium for walking graph: pip install osmium")
    if not stops:
        raise ValueError("Walking graph needs transport stops")
    # The graph is the union of 15-minute catchments around all stops.
    # Any true path to a stop within 1200 m stays within that stop's circle.
    # A small margin permits snapping and short OSM edges at the boundary.
    started = time.monotonic()
    print("Pedestrian graph: reading OSM nodes near transit stops...")
    points = WalkNodeCollector(stops, origin, limit_m)
    points.apply_file(str(source_path))
    print(f"pedestrian candidate nodes: {len(points.nodes)}")
    # Node and stop_area discovery piggybacks on WalkNodeCollector's PBF pass.
    entrances = points.entrances
    print("Pedestrian graph: reading walkable highways...")
    ways = WalkWayCollector(
        points.nodes,
        points.blocked,
        allow_private_station_ways,
        station_way_ids={
            int(stop["id"][1:]) for stop in stops if stop["id"].startswith("w")
        },
        node_levels=points.node_levels,
    )
    ways.apply_file(str(source_path))
    entrances.resolve(
        stops,
        ways.station_way_names,
        ways.station_way_centers,
        ways.platform_way_ids,
    )
    platform_link_audit = link_platform_surfaces(
        ways.graph_nodes,
        ways.edges,
        ways.platform_polygons,
        ways.walk_stubs,
        ways.station_way_nodes,
    )
    entrances._way_platform_names = ways.station_way_names
    platform_match_audit = match_nearby_platform_polygons(
        entrances, stops, ways.platform_polygons, ways.graph_nodes
    )
    # Platforms can be mapped as nodes, open ways, or closed OSM areas.
    point_match_audit = match_linear_and_point_platforms(
        entrances,
        stops,
        {
            id: items
            for id, items in ways.station_way_nodes.items()
            if id in ways.platform_way_ids
        },
        ways.graph_nodes,
    )
    boarding_select_audit = choose_boarding_platforms(
        entrances, stops, ways.station_way_nodes, ways.graph_nodes
    )
    facility_match_audit = match_station_facility_anchors(entrances, stops)
    del points
    if allow_private_station_ways:
        found = sorted(ways.accepted_private_way_ids)
        absent = sorted(MOSKVORECHYE_PRIVATE_WAYS - set(found))
        print("[station] permitted private OSM ways with graph edges:", found)
        if absent:
            print("[station] WARNING: missing or unusable test ways:", absent)
    print(f"pedestrian walkable ways: {ways.ways_count}")
    print(f"pedestrian walking segments: {len(ways.edges)}")
    (
        exterior_parents,
        exterior_roots,
        trusted_roots,
        exterior_edges,
        preferred_exterior,
    ) = exterior_component_edges(
        len(ways.graph_nodes),
        ways.edges,
        ways.exterior_edges,
        ways.preferred_exterior_edges,
    )
    platform_stops = inferred_platform_point_stops(
        entrances, ways, exterior_parents, trusted_roots
    )
    used_entrances = {
        entrance_id for area in entrances.access.values() for entrance_id in area
    }
    entrance_stops = [
        {
            "id": "n" + str(entrance_id),
            "entrance_id": entrance_id,
            "x": entry[0],
            "y": entry[1],
            "snap_limit": 35.0,
            "modes": [],
        }
        for entrance_id, entry in entrances.entrances.items()
        if entrance_id in used_entrances
    ]
    # Facility points are station identity markers, not stop_area doors.
    # They require a nearby mapped public outdoor footway; otherwise they
    # remain unattached and cannot invent access across railway tracks.
    used_facilities = {
        facility_id
        for matches in entrances.facility_access.values()
        for facility_id in matches
    }
    facility_stops = [
        {
            "id": "n" + str(facility_id),
            "entrance_id": facility_id,
            "facility_id": facility_id,
            "x": facility["x"],
            "y": facility["y"],
            "snap_limit": 45.0,
            "facility_only_footways": True,
            "modes": [],
        }
        for facility_id, facility in entrances.facility_nodes.items()
        if facility_id in used_facilities
    ]
    all_stops = stops + entrance_stops + facility_stops + platform_stops
    graph, missed = attach_stops_to_walk_graph(
        ways.graph_nodes,
        ways.edges,
        all_stops,
        preferred_edges=(ways.preferred_edges if allow_private_station_ways else None),
        osm_node_index=ways.graph_index,
        osm_way_index=ways.station_way_nodes,
        street_edges=exterior_edges,
        street_nodes=ways.street_nodes,
        station_access_ids=set(entrances.access),
        exterior_parents=exterior_parents,
        exterior_roots=exterior_roots,
        trusted_exterior_roots=trusted_roots,
        preferred_exterior_edges=preferred_exterior,
        railway_segments=ways.surface_railway_segments,
    )
    del exterior_edges, preferred_exterior
    snapped_platform_nodes = defaultdict(list)
    for point in platform_stops:
        if point.get("walk_node", -1) >= 0:
            snapped_platform_nodes[point["platform_key"]].append(point["walk_node"])
    add_station_access_links(
        graph,
        stops,
        entrances,
        entrance_stops,
        snapped_platform_nodes=snapped_platform_nodes,
        osm_node_index=ways.graph_index,
        osm_way_index=ways.station_way_nodes,
        exterior_parents=exterior_parents,
        exterior_roots=exterior_roots,
    )
    facility_link_audit = add_station_facility_fallback(
        graph, stops, entrances, facility_stops
    )
    graph["interchange_hints"] = station_interchange_hints(entrances, stops)
    graph["rail_access_quality"] = rail_access_quality_report(
        graph, stops, exterior_roots, trusted_roots
    )
    graph["platform_link_audit"] = {
        **platform_link_audit,
        **platform_match_audit,
        **facility_match_audit,
        **facility_link_audit,
        **point_match_audit,
        **boarding_select_audit,
        "inferred_platform_endpoint_anchors": len(platform_stops),
        "attached_platform_endpoint_anchors": sum(
            point.get("walk_node", -1) >= 0 for point in platform_stops
        ),
    }
    if not graph["edges"]:
        raise RuntimeError("No walking edges extracted from the source PBF")
    print(f"pedestrian graph nodes: {len(graph['nodes'])}")
    print(f"pedestrian stops without walkway: {sum(missed.values())}")
    print(f"pedestrian graph build took: {time.monotonic()-started:.1f}s")
    return graph


def rail_access_quality_report(graph, stops, mapped_street_roots, major_street_roots):
    """Validate final weak connectivity, AFTER inserting station connectors.

    This is not a directed pedestrian reachability proof: one-way entrance
    constraints are still enforced by the routing algorithm. The report
    distinguishes mapped topology from heuristic boarding connections.
    """
    count = len(graph["nodes"])
    parent = array("I", range(count))
    size = array("I", [1]) * count

    def find(node, parents=parent):
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    for a, b, _distance, _direction in graph["edges"]:
        a, b = find(a), find(b)
        if a != b:
            if size[a] < size[b]:
                a, b = b, a
            parent[b] = a
            size[a] += size[b]
    major = {find(node) for node in major_street_roots if node < count}
    mapped = {find(node) for node in mapped_street_roots if node < count}
    counts = Counter()
    by_mode = defaultdict(Counter)
    samples = []
    for stop in stops:
        modes = set(stop.get("modes", ())) & StationEntranceCollector.RAIL_MODES
        if not modes:
            continue
        node = stop.get("walk_node", -1)
        if node < 0 or node >= count:
            status = "unattached"
        else:
            root = find(node)
            if root in major:
                status = "connected_to_major_street"
            elif root in mapped:
                status = "connected_to_short_mapped_street"
            else:
                status = "isolated_from_mapped_street"
        counts[status] += 1
        for mode in modes:
            by_mode[mode][status] += 1
        if status != "connected_to_major_street" and len(samples) < 120:
            samples.append(
                {
                    "id": stop["id"],
                    "name": stop.get("name"),
                    "mode": sorted(modes),
                    "status": status,
                    "source": stop.get("station_access_kind", "none"),
                }
            )
    del find, parent, size
    print("[rail-quality] final graph components:", dict(counts), flush=True)
    return {
        "metric": "weak_connectivity_not_directed_access",
        "counts": dict(counts),
        "by_mode": {key: dict(value) for key, value in sorted(by_mode.items())},
        "review_samples": samples,
    }


def stop_fingerprint(stops):
    digest = hashlib.sha256()
    for stop in stops:
        digest.update(
            (
                f"{stop['id']}:{stop['lat']:.6f}:{stop['lon']:.6f}:"
                f"{','.join(stop.get('modes', ()))};"
            ).encode("ascii")
        )
    return digest.hexdigest()


def load_or_build_pedestrian(
    cache_dir, source, index, limit_m, rebuild=False, allow_private_station_ways=False
):
    filename = (
        PEDESTRIAN_TEST_CACHE_NAME
        if allow_private_station_ways
        else PEDESTRIAN_CACHE_NAME
    )
    path = cache_dir / filename
    cache_version = WALK_CACHE_VERSION + int(allow_private_station_ways)
    fingerprint = stop_fingerprint(index["stops"])
    source_path = Path(source)
    source_stat = source_path.stat() if source_path.is_file() else None
    source_key = (
        [source_stat.st_size, source_stat.st_mtime_ns]
        if source_stat is not None
        else None
    )
    if path.is_file() and not rebuild:
        with gzip.open(path, "rt", encoding="ascii") as stream:
            cached = json.load(stream)
        if (
            cached.get("version") == cache_version
            and cached.get("stop_fingerprint") == fingerprint
            and cached.get("walk_limit_m") == limit_m
            and cached.get("source_key") == source_key
        ):
            print(f"Using cached: {path}")
            graph = cached["walking"]
            kinds = cached.get("stop_access_kinds") or []
            for i, (stop, node) in enumerate(zip(index["stops"], cached["stop_nodes"])):
                stop["walk_node"] = node
                if i < len(kinds) and kinds[i]:
                    stop["station_access_kind"] = kinds[i]
            return graph
        print("Pedestrian cache differs from transport stops; rebuilding...")
    graph = build_pedestrian_graph(
        source,
        index["stops"],
        index["origin"],
        limit_m,
        allow_private_station_ways=allow_private_station_ways,
    )
    content = {
        "version": cache_version,
        "stop_fingerprint": fingerprint,
        "walk_limit_m": limit_m,
        "source_key": source_key,
        "allowed_private_ways": (
            sorted(MOSKVORECHYE_PRIVATE_WAYS) if allow_private_station_ways else []
        ),
        "stop_nodes": [stop["walk_node"] for stop in index["stops"]],
        "stop_access_kinds": [
            stop.get("station_access_kind") for stop in index["stops"]
        ],
        "walking": graph,
    }
    tmp = path.with_suffix(".tmp.gz")
    with gzip.open(tmp, "wt", encoding="ascii", compresslevel=6) as stream:
        json.dump(content, stream, ensure_ascii=True, separators=(",", ":"))
    tmp.replace(path)
    print(f"Saved pedestrian cache: {path}")
    return graph


# Offline walking transfer graph: directed CSR with network shortest paths.
# This is distinct from the full OSM graph needed for on-map coverage.
WALK_TRANSFER_CACHE_VERSION = 11


def merge_interchange_hints(offsets, targets, meters, hints, stop_count):
    """Merge organizational interchange estimates into directed transfer CSR.

    Existing measured pedestrian routes always win over estimated ones. Only
    missing transfer targets are added; hints do not alter walking geometry.
    """
    if not hints:
        return offsets, targets, meters
    by_source = defaultdict(list)
    for a, b, distance, _relation_id in hints:
        if 0 <= a < stop_count and 0 <= b < stop_count and a != b:
            by_source[a].append((b, distance))
    new_offsets = [0]
    new_targets = []
    new_meters = []
    inserted = 0
    for source in range(stop_count):
        current = dict(
            zip(
                targets[offsets[source] : offsets[source + 1]],
                meters[offsets[source] : offsets[source + 1]],
            )
        )
        for target, distance in by_source.get(source, ()):
            if target not in current:
                current[target] = distance
                inserted += 1
        for target, length in sorted(current.items()):
            new_targets.append(target)
            new_meters.append(length)
        new_offsets.append(len(new_targets))
    print("[station] estimated interchange arcs added to transfers:", inserted)
    return new_offsets, new_targets, new_meters


def build_walk_transfers(walking, stops, radius_m):
    started = time.monotonic()
    count = len(walking["nodes"])
    stop_nodes = defaultdict(list)
    for index, stop in enumerate(stops):
        node = stop.get("walk_node", -1)
        if isinstance(node, int) and 0 <= node < count:
            stop_nodes[node].append(index)

    # Directed adjacency exactly matches the browser's walking CSR.
    degree = array("I", [0]) * count
    for a, b, length, direction in walking["edges"]:
        if direction not in (3, 13):
            degree[a] += 1
        if direction not in (2, 12):
            degree[b] += 1
    offsets = array("I", [0]) * (count + 1)
    for node in range(count):
        offsets[node + 1] = offsets[node] + degree[node]
    to = array("I", [0]) * offsets[-1]
    weight = array("f", [0.0]) * offsets[-1]
    cursor = array("I", offsets[:-1])
    for a, b, length, direction in walking["edges"]:
        if direction not in (3, 13):
            slot = cursor[a]
            to[slot] = b
            weight[slot] = length
            cursor[a] += 1
        if direction not in (2, 12):
            slot = cursor[b]
            to[slot] = a
            weight[slot] = length
            cursor[b] += 1
    del degree, cursor

    result_offsets = [0]
    result_to = []
    result_meters = []
    # A search is shared by all stops attached to the same graph vertex.
    # Bounded reuse for stations sharing an identical attachment node.
    reached_by_node = OrderedDict()
    for stop_index, stop in enumerate(stops):
        source = stop.get("walk_node", -1)
        if not isinstance(source, int) or source < 0 or source >= count:
            result_offsets.append(len(result_to))
            continue
        if source in reached_by_node:
            reached = reached_by_node[source]
            reached_by_node.move_to_end(source)
        else:
            best = {source: 0.0}
            heap = [(0.0, source)]
            reached = []
            while heap:
                distance, node = heapq.heappop(heap)
                if distance > radius_m + 0.00001:
                    break
                if distance > best[node] + 0.00001:
                    continue
                for other_stop in stop_nodes.get(node, ()):
                    reached.append((other_stop, round(distance, 2)))
                for edge in range(offsets[node], offsets[node + 1]):
                    next_node = to[edge]
                    next_distance = distance + weight[edge]
                    if next_distance > radius_m + 0.00001:
                        continue
                    if next_distance + 0.00001 >= best.get(next_node, math.inf):
                        continue
                    best[next_node] = next_distance
                    heapq.heappush(heap, (next_distance, next_node))
            reached.sort()
            reached_by_node[source] = reached
            if len(reached_by_node) > 256:
                reached_by_node.popitem(last=False)
        for target, distance in reached:
            result_to.append(target)
            result_meters.append(distance)
        result_offsets.append(len(result_to))
        if (stop_index + 1) % 1000 == 0:
            elapsed = time.monotonic() - started
            print(
                "  transfer graph:",
                stop_index + 1,
                "/",
                len(stops),
                "stops;",
                len(result_to),
                "arcs;",
                f"{elapsed:.1f}s",
            )

    result_offsets, result_to, result_meters = merge_interchange_hints(
        result_offsets,
        result_to,
        result_meters,
        walking.get("interchange_hints", ()),
        len(stops),
    )
    print(
        "walking transfer graph:",
        len(result_to),
        "arcs in",
        f"{time.monotonic()-started:.1f}s",
    )
    return {"offsets": result_offsets, "to": result_to, "meters": result_meters}


def check_moskvorechye_transfers(transfers, stops):
    """Verify directed reachable street stops, not just weak connectivity."""
    count = 0
    for i, rail in enumerate(stops):
        if rail["id"] not in MOSKVORECHYE_PLATFORM_IDS:
            continue
        matches = []
        start, end = transfers["offsets"][i : i + 2]
        for pos in range(start, end):
            other = stops[transfers["to"][pos]]
            if not (set(other.get("modes", ())) & SURFACE_MODES):
                continue
            if math.hypot(rail["x"] - other["x"], rail["y"] - other["y"]) > 700.0:
                continue
            matches.append((other["id"], round(transfers["meters"][pos])))
        count += len(matches)
        print(
            "[station] Directed walking transfers FROM",
            rail["id"],
            "to nearby surface stops:",
            len(matches),
            flush=True,
        )
        for osm_id, meters in matches[:5]:
            print("[station]   ->", osm_id, meters, "m", flush=True)
    if count == 0:
        print(
            "[station] WARNING: still no directed platform-to-street path!", flush=True
        )
    else:
        print("[station] DIRECTED CONNECTION PASS", flush=True)
    return count > 0


def load_or_build_walk_transfers(
    cache_dir,
    walking_source,
    walking,
    stops,
    radius_m,
    rebuild=False,
    allow_private_station_ways=False,
):
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        TRANSFERS_TEST_CACHE_NAME
        if allow_private_station_ways
        else TRANSFERS_CACHE_NAME
    )
    path = cache_dir / filename
    source = Path(walking_source)
    stamp = source.stat() if source.is_file() else None
    source_key = (
        [str(source.resolve()), stamp.st_size, stamp.st_mtime_ns] if stamp else None
    )
    fingerprint = hashlib.sha256()
    for stop in stops:
        fingerprint.update(
            (str(stop["id"]) + ":" + str(stop.get("walk_node", -1)) + ";").encode(
                "utf-8"
            )
        )
    metadata = {
        "version": WALK_TRANSFER_CACHE_VERSION,
        "stop_fingerprint": fingerprint.hexdigest(),
        "source": source_key,
        "radius_m": radius_m,
        "nodes": len(walking["nodes"]),
        "edges": len(walking["edges"]),
    }
    if path.is_file() and not rebuild:
        with gzip.open(path, "rt", encoding="ascii") as stream:
            cached = json.load(stream)
        if all(cached.get(key) == value for key, value in metadata.items()):
            graph = cached["transfers"]
            if (
                len(graph.get("offsets", ())) == len(stops) + 1
                and graph["offsets"][-1] == len(graph.get("to", ()))
                and len(graph["to"]) == len(graph.get("meters", ()))
            ):
                print(f"Using cached walking transfers: {path}")
                return graph
        print("Walking transfer cache differs; rebuilding...")
    graph = build_walk_transfers(walking, stops, radius_m)
    content = {**metadata, "transfers": graph}
    temp = path.with_suffix(".tmp.gz")
    with gzip.open(temp, "wt", encoding="ascii", compresslevel=6) as stream:
        json.dump(content, stream, ensure_ascii=True, separators=(",", ":"))
    temp.replace(path)
    print(f"Saved walking transfer cache: {path}")
    return graph


# Transport graph and spatial index


def project(lon, lat, origin_lon, origin_lat):
    origin_lat_rad = math.radians(origin_lat)

    x = EARTH_RADIUS_M * math.radians(lon - origin_lon) * math.cos(origin_lat_rad)

    y = EARTH_RADIUS_M * math.radians(lat - origin_lat)

    return x, y


def conservative_geometry_edge_lengths(geometry, direction_stops, stops, mode):
    """Use mapped route distances only where projection is unambiguous enough.

    Stop-to-stop geometry is accepted only with close snaps, forward order,
    coherent constituent OSM ways, and plausible length versus the chord.
    Rejected edges retain the original modal circuity estimate in the UI.
    """
    count = max(0, len(direction_stops) - 1)
    rejected = [None] * count
    if not geometry or count == 0:
        return rejected
    kind = geometry.get("type")
    if kind not in ("LineString", "MultiLineString"):
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
    for i in range(count):
        left_snap, left_pos = located[i]
        right_snap, right_pos = located[i + 1]
        if left_snap > max_snap or right_snap > max_snap:
            accepted.append(None)
            continue
        distance = right_pos - left_pos
        if distance <= 0 or any(left_pos < b < right_pos for b in breaks):
            accepted.append(None)
            continue
        start = stops[direction_stops[i]]
        end = stops[direction_stops[i + 1]]
        straight = coord_distance_m(
            (start["lon"], start["lat"]), (end["lon"], end["lat"])
        )
        if straight < 25.0 or distance < straight * 0.98:
            accepted.append(None)
            continue
        if distance > max(straight * 4.0, straight + 300.0):
            accepted.append(None)
            continue
        accepted.append(round(distance, 1))
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

        if props.get("roundtrip"):
            route["roundtrip"] = props.get("roundtrip")

        mapped_edge_meters = [None] * (len(direction_stops) - 1)
        if props.get("geometry_source") == "osm_ways":
            mapped_edge_meters = conservative_geometry_edge_lengths(
                geometry, direction_stops, stops, route["mode"]
            )
        route["directions"].append(
            {
                "relation_id": props.get("relation_id"),
                "direction_id": f"{route_key}:{props.get('relation_id')}:"
                f"{len(route['directions'])}",
                "from": props.get("from"),
                "to": props.get("to"),
                "roundtrip": props.get("roundtrip"),
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
    }


# The named corridor audit is diagnostic: OSM may use old platform names or
# omit route members. Never synthesize train service between nearby stations.
PAVELETSKY_STATIONS = (
    "\u043f\u0430\u0432\u0435\u043b\u0435\u0446\u043a",
    "\u043d\u0430\u0433\u0430\u0442\u0438\u043d",
    "\u0432\u0430\u0440\u0448\u0430\u0432\u0441\u043a",
    "\u0447\u0435\u0440\u0442\u0430\u043d\u043e\u0432",
    "\u0431\u0438\u0440\u044e\u043b\u0451\u0432",
    "\u0431\u0438\u0440\u044e\u043b\u0435\u0432",
    "\u0431\u0443\u043b\u0430\u0442\u043d\u0438\u043a",
    "\u0440\u0430\u0441\u0442\u043e\u0440\u0433\u0443\u0435\u0432",
    "\u0434\u043e\u043c\u043e\u0434\u0435\u0434\u043e\u0432",
    "\u043b\u0435\u043d\u0438\u043d\u0441\u043a",
    "\u0430\u0432\u0438\u0430\u0446\u0438\u043e\u043d",
    "\u0432\u0437\u043b\u0451\u0442\u043d",
    "\u0432\u0437\u043b\u0435\u0442\u043d",
)


def audit_paveletsky_corridor(index, feature_collection):
    """Record which REAL train relations cover the southern rail corridor."""
    stops = index["stops"]
    audit = feature_collection.get("extraction_audit", {})
    missing_ids = {
        row["relation_id"]: row["missing"]
        for row in audit.get("rail_routes_with_missing_stops", [])
    }
    route_rows = []
    affected_stops = set()
    for route_key, route in index["routes"].items():
        if route.get("mode") not in ("mcd", "train"):
            continue
        for direction in route["directions"]:
            ordered = [stops[i] for i in direction["stops"]]
            if not any(
                any(
                    term in (stop.get("name") or "").casefold()
                    for term in PAVELETSKY_STATIONS
                )
                for stop in ordered
            ):
                continue
            affected_stops.update(direction["stops"])
            gaps = []
            for left, right in zip(ordered, ordered[1:]):
                meters = stop_distance_m(left, right)
                if meters >= 4000.0:
                    gaps.append(
                        {
                            "from": left.get("name"),
                            "to": right.get("name"),
                            "meters": round(meters),
                        }
                    )
            route_rows.append(
                {
                    "route_key": route_key,
                    "relation_id": direction.get("relation_id"),
                    "unresolved_osm_members": missing_ids.get(
                        direction.get("relation_id"), 0
                    ),
                    "mode": route.get("mode"),
                    "ref": route.get("ref"),
                    "from": direction.get("from"),
                    "to": direction.get("to"),
                    "stop_count": len(ordered),
                    "stop_names": [stop.get("name") for stop in ordered],
                    "long_stop_gaps": gaps,
                    "missing_walk_nodes": [
                        stop.get("name")
                        for stop in ordered
                        if stop.get("walk_node", -1) < 0
                    ],
                }
            )
    station_rows = []
    for i in sorted(affected_stops):
        stop = stops[i]
        name = (stop.get("name") or "").casefold()
        if any(term in name for term in PAVELETSKY_STATIONS):
            station_rows.append(
                {
                    "id": stop["id"],
                    "name": stop.get("name"),
                    "modes": stop.get("modes", []),
                    "walk_node": stop.get("walk_node", -1),
                    "route_count": len(stop["routes"]),
                }
            )
    print(
        "[paveletsky] rail directions:",
        len(route_rows),
        "named stops:",
        len(station_rows),
        "long gaps:",
        sum(len(r["long_stop_gaps"]) for r in route_rows),
        flush=True,
    )
    return {
        "directions": route_rows,
        "named_stops": station_rows,
        "missing_members_by_mode": audit.get("missing_stop_members_by_mode", {}),
        "note": (
            "Large gaps may be express-service skips. OSM route membership "
            "does not authorize creating extra boarding stops."
        ),
    }


# Index diagnostics


def stop_distance_m(a, b):
    lat_a = math.radians(a["lat"])
    lat_b = math.radians(b["lat"])
    dlon = math.radians(a["lon"] - b["lon"])
    dlat = lat_a - lat_b
    value = math.sin(dlat / 2) ** 2
    value += math.cos(lat_a) * math.cos(lat_b) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1, math.sqrt(value)))


def audit_rail_stations(index, feature_collection):
    """Diagnose routing presence and walking attachment; never invent links."""
    stations = feature_collection.get("extraction_audit", {}).get(
        "named_station_candidates", []
    )
    result = []
    for term in RAIL_AUDIT_TERMS:
        matched = []
        for stop in index["stops"]:
            if term not in (stop.get("name") or "").casefold():
                continue
            modes = sorted(stop.get("modes", ()))
            matched.append(
                {
                    "id": stop["id"],
                    "name": stop.get("name"),
                    "lon": stop["lon"],
                    "lat": stop["lat"],
                    "modes": modes,
                    "walk_node": stop.get("walk_node", -1),
                    "walking_connected": stop.get("walk_node", -1) >= 0,
                    "route_keys": stop.get("routes", []),
                }
            )
        candidates = [
            station
            for station in stations
            if term in (station.get("name") or "").casefold()
        ]
        nearby = []
        for candidate in candidates:
            best = []
            for stop in index["stops"]:
                meters = stop_distance_m(candidate, stop)
                if meters <= 350:
                    best.append(
                        {
                            "id": stop["id"],
                            "name": stop.get("name"),
                            "meters": round(meters),
                            "modes": stop.get("modes", []),
                            "walk_node": stop.get("walk_node", -1),
                        }
                    )
            best.sort(key=lambda item: item["meters"])
            nearby.append(
                {
                    "station": candidate["name"],
                    "station_osm_id": candidate["id"],
                    "nearest_route_stops": best[:12],
                }
            )
        result.append(
            {
                "query": term,
                "stops": matched,
                "osm_station_candidates": candidates,
                "nearby_route_stops": nearby,
            }
        )
    return result


def audit_station_walk_components(walking, stops):
    """Report weak components of selected rail stops without inventing edges.

    Components are an undirected lower bound: different components imply no
    walking route, while one component does not guarantee directed reachability.
    """
    started = time.monotonic()
    count = len(walking["nodes"])
    parent = array("I", range(count))
    rank = bytearray(count)

    def find(node, parents=parent):
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    for a, b, _length, _direction in walking["edges"]:
        a, b = find(a), find(b)
        if a == b:
            continue
        if rank[a] < rank[b]:
            a, b = b, a
        parent[b] = a
        if rank[a] == rank[b]:
            rank[a] += 1

    # Sparse sizes are sufficient: only components containing station/stop
    # attachments need descriptions in the report.
    matched = []
    roots = set()
    for idx, stop in enumerate(stops):
        if not relevant_rail_audit_name(stop.get("name")):
            continue
        node = stop.get("walk_node", -1)
        if node < 0 or node >= count:
            root = None
        else:
            root = find(node)
            roots.add(root)
        matched.append((idx, stop, root))

    stop_roots = []
    for stop in stops:
        node = stop.get("walk_node", -1)
        stop_roots.append(find(node) if 0 <= node < count else None)

    sizes = {root: 0 for root in roots}
    for node in range(count):
        root = find(node)
        if root in sizes:
            sizes[root] += 1
    del find, parent, rank

    nearby = []
    for idx, stop, root in matched:
        if (
            "mcd" not in stop.get("modes", ())
            and "mcc" not in stop.get("modes", ())
            and "train" not in stop.get("modes", ())
        ):
            continue
        street = []
        for candidate_idx, other in enumerate(stops):
            if candidate_idx == idx:
                continue
            dist = math.hypot(stop["x"] - other["x"], stop["y"] - other["y"])
            if dist > 600.0:
                continue
            other_node = other.get("walk_node", -1)
            if other_node < 0:
                continue
            street.append((round(dist), candidate_idx, stop_roots[candidate_idx]))
        street.sort()
        nearby.append((idx, stop, root, street[:36]))

    result = []
    for idx, stop, root, neighbours in nearby:
        result.append(
            {
                "id": stop["id"],
                "name": stop.get("name"),
                "stop_index": idx,
                "walk_node": stop.get("walk_node", -1),
                "component": root,
                "component_nodes": sizes.get(root, 0),
                "nearby_stop_candidates": [
                    {
                        "stop_index": other_idx,
                        "id": stops[other_idx]["id"],
                        "name": stops[other_idx].get("name"),
                        "meters": meters,
                        "modes": stops[other_idx].get("modes", ()),
                        "same_component": (candidate_root == root),
                    }
                    for meters, other_idx, candidate_root in neighbours[:12]
                ],
            }
        )
    print(
        "[audit] station walk components:",
        len(result),
        "entries in",
        f"{time.monotonic()-started:.1f}s",
        flush=True,
    )
    for item in result:
        print(
            "[audit]",
            item["name"],
            item["id"],
            "component nodes:",
            item["component_nodes"],
            flush=True,
        )
        street = [
            entry
            for entry in item["nearby_stop_candidates"]
            if set(entry["modes"]) & SURFACE_MODES
        ]
        for entry in street[:3]:
            print(
                "[audit]   street stop:",
                entry["name"],
                "distance:",
                entry["meters"],
                "m; same component:",
                entry["same_component"],
                flush=True,
            )
    return result


def rail_boarding_access_report(stops):
    """Explain boarding access, unlike an ambiguous walk_node-only counter."""
    modes = ("metro", "mcc", "mcd", "train")
    by_mode = {}
    for mode in modes:
        relevant = [s for s in stops if mode in s.get("modes", ())]
        sources = Counter(
            s.get(
                "station_access_kind",
                "other_mapped_walk" if s.get("walk_node", -1) >= 0 else "unattached",
            )
            for s in relevant
        )
        failures = [
            {
                "id": s["id"],
                "name": s.get("name"),
                "lon": s.get("lon"),
                "lat": s.get("lat"),
            }
            for s in relevant
            if s.get("walk_node", -1) < 0
        ]
        by_mode[mode] = {
            "stops": len(relevant),
            "access_sources": dict(sources),
            "missing_access_count": len(failures),
            "missing_access_samples": failures[:100],
        }
        print(
            "[rail-board]",
            mode,
            "total:",
            len(relevant),
            "access:",
            dict(sources),
            "missing:",
            len(failures),
        )
    return by_mode


def audit_all_rail_access(walking, stops, radius_m=650.0):
    """Screen all rail stops for disconnected street access using weak components.

    A small component or disconnected nearby surface stop is only a candidate
    problem. Connected weak components do not establish legal or directed access.
    This pass uses cached walking data and does not create routing edges.
    """
    started = time.monotonic()
    parent, sizes = walk_weak_components(walking)
    node_count = len(parent)
    street_grid = defaultdict(list)
    cell_m = 400.0
    for index, stop in enumerate(stops):
        if not set(stop.get("modes", ())) & SURFACE_MODES:
            continue
        x, y = stop["x"], stop["y"]
        key = (math.floor(x / cell_m), math.floor(y / cell_m))
        street_grid[key].append(index)

    search_cells = math.ceil(radius_m / cell_m)
    entries = []
    statuses = Counter()
    rail_modes = {"metro", "mcc", "mcd", "train"}
    for index, stop in enumerate(stops):
        if not set(stop.get("modes", ())) & rail_modes:
            continue
        node = stop.get("walk_node", -1)
        root = (
            parent[node] if isinstance(node, int) and 0 <= node < node_count else None
        )
        nearby = []
        x, y = stop["x"], stop["y"]
        cell_x = math.floor(x / cell_m)
        cell_y = math.floor(y / cell_m)
        for gx in range(cell_x - search_cells, cell_x + search_cells + 1):
            for gy in range(cell_y - search_cells, cell_y + search_cells + 1):
                for other_index in street_grid.get((gx, gy), ()):
                    other = stops[other_index]
                    distance = math.hypot(x - other["x"], y - other["y"])
                    if distance > radius_m:
                        continue
                    other_node = other.get("walk_node", -1)
                    other_root = (
                        parent[other_node]
                        if isinstance(other_node, int) and 0 <= other_node < node_count
                        else None
                    )
                    nearby.append((distance, other_index, other_root))
        nearby.sort()
        connected = any(
            other_root == root for _, _, other_root in nearby if root is not None
        )
        if root is None:
            status = "unattached"
        elif sizes[root] < 100 and not connected:
            status = "small_component"
        elif nearby and not connected:
            status = "nearby_surface_disconnected"
        elif not nearby:
            status = "no_surface_stop_nearby"
        else:
            status = "same_weak_component"
        statuses[status] += 1
        entries.append(
            {
                "stop_index": index,
                "id": stop["id"],
                "name": stop.get("name"),
                "modes": sorted(stop.get("modes", ())),
                "lon": stop["lon"],
                "lat": stop["lat"],
                "walk_node": node,
                "component": root,
                "component_nodes": sizes.get(root, 0),
                "status": status,
                "nearby_surface_count": len(nearby),
                "same_component_surface_count": sum(
                    candidate_root == root
                    for _, _, candidate_root in nearby
                    if root is not None
                ),
                "nearest_surface": [
                    {
                        "id": stops[candidate_index]["id"],
                        "name": stops[candidate_index].get("name"),
                        "meters": round(distance),
                        "same_component": root is not None and candidate_root == root,
                    }
                    for distance, candidate_index, candidate_root in nearby[:8]
                ],
            }
        )
    rank = {
        "unattached": 0,
        "small_component": 1,
        "nearby_surface_disconnected": 2,
        "no_surface_stop_nearby": 3,
        "same_weak_component": 4,
    }
    entries.sort(
        key=lambda entry: (
            rank[entry["status"]],
            entry["component_nodes"],
            entry["name"] or "",
        )
    )
    print("[rail-access] weak component screen:", dict(statuses), flush=True)
    issues_shown = 0
    for entry in entries:
        if entry["status"] not in (
            "unattached",
            "small_component",
            "nearby_surface_disconnected",
        ):
            continue
        if entry["name"]:
            print(
                "[rail-access] REVIEW:",
                entry["name"],
                entry["id"],
                "status:",
                entry["status"],
                "nodes:",
                entry["component_nodes"],
                flush=True,
            )
            issues_shown += 1
        if issues_shown >= 30:
            break
    print("[rail-access] completed in", f"{time.monotonic()-started:.1f}s")
    return {
        "description": (
            "Candidate problems only: weak connectivity is not a public or "
            "directed walking-access guarantee. No synthetic links inserted."
        ),
        "radius_m": radius_m,
        "summary": dict(statuses),
        "stations": entries,
    }


# Offline station-access investigation. This is deliberately separate from the
# map engine: no candidate gap becomes a routing edge without OSM evidence.


def walk_weak_components(walking):
    size = len(walking["nodes"])
    parent = array("I", range(size))
    rank = bytearray(size)

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, _length, _direction in walking["edges"]:
        a, b = root(a), root(b)
        if a == b:
            continue
        if rank[a] < rank[b]:
            a, b = b, a
        parent[b] = a
        if rank[a] == rank[b]:
            rank[a] += 1
    counts = Counter()
    for i in range(size):
        component = root(i)
        parent[i] = component
        counts[component] += 1
    return parent, counts


def check_moskvorechye_connection(walking, stops):
    """Report whether station platforms join nearby street stop components."""
    rail = [stop for stop in stops if stop["id"] in MOSKVORECHYE_PLATFORM_IDS]
    if len(rail) != 2:
        print("[station] FAIL: two Moskvo-rechye platform nodes not found")
        return False
    parent, sizes = walk_weak_components(walking)
    roots = []
    for stop in rail:
        node = stop.get("walk_node", -1)
        root = int(parent[node]) if 0 <= node < len(parent) else None
        roots.append(root)
        print(
            "[station] platform:",
            stop["id"],
            "component:",
            root,
            "nodes:",
            sizes.get(root, 0),
            flush=True,
        )
    street = []
    for stop in stops:
        if not (set(stop.get("modes", ())) & SURFACE_MODES):
            continue
        if (
            min(
                math.hypot(stop["x"] - target["x"], stop["y"] - target["y"])
                for target in rail
            )
            > 700
        ):
            continue
        node = stop.get("walk_node", -1)
        if 0 <= node < len(parent):
            root = int(parent[node])
            street.append((stop, root))
    reachable = [(stop, root) for stop, root in street if root in roots]
    print(
        "[station] nearby street stops:",
        len(street),
        "connected to platforms:",
        len(reachable),
        flush=True,
    )
    for stop, root in reachable[:6]:
        print(
            "[station]   reachable street stop:",
            stop["id"],
            stop.get("name"),
            "component:",
            root,
            flush=True,
        )
    # This verifies weak graph connectivity. It does not verify public access
    # rights, correct OSM topology or directed walking reachability.
    ok = bool(reachable) and roots[0] == roots[1]
    print("[station] CONNECTION", "PASS" if ok else "FAIL", flush=True)
    return ok


class LocalStationNodes(OsmiumSimpleHandler):
    """Read only local OSM node coordinates from the cached PBF."""

    def __init__(self, origin, x, y, radius):
        super().__init__()
        self.origin = origin
        self.center = (x, y)
        self.radius2 = (radius + 80.0) ** 2
        self.coords = {}
        self.tags = {}

    def node(self, node):
        if not node.location.valid():
            return
        x, y = project(node.location.lon, node.location.lat, *self.origin)
        dx, dy = x - self.center[0], y - self.center[1]
        if dx * dx + dy * dy > self.radius2:
            return
        self.coords[node.id] = (round(x, 1), round(y, 1))
        tags = {
            k: node.tags.get(k)
            for k in (
                "highway",
                "railway",
                "entrance",
                "indoor",
                "level",
                "layer",
                "bridge",
                "tunnel",
                "barrier",
                "foot",
                "access",
                "name",
                "public_transport",
                "wheelchair",
            )
            if node.tags.get(k)
        }
        if tags:
            self.tags[node.id] = tags


class LocalStationWays(OsmiumSimpleHandler):
    """Collect nearby ways, including candidates rejected by walking filter."""

    def __init__(self, local_nodes, coordinate_components, allow_private_ways=False):
        super().__init__()
        self.nodes = local_nodes
        self.components = coordinate_components
        self.allow_private_ways = allow_private_ways
        self.ways = []
        self.node_ways = defaultdict(list)

    def way(self, way):
        refs = [ref.ref for ref in way.nodes]
        present = [ref for ref in refs if ref in self.nodes.coords]
        if len(present) < 2:
            return
        tags = {
            k: way.tags.get(k)
            for k in (
                "highway",
                "railway",
                "public_transport",
                "footway",
                "indoor",
                "bridge",
                "tunnel",
                "layer",
                "level",
                "foot",
                "access",
                "sidewalk",
                "oneway:foot",
                "area",
                "name",
                "barrier",
            )
            if way.tags.get(k)
        }
        if not any(
            k in tags
            for k in (
                "highway",
                "public_transport",
                "railway",
                "indoor",
            )
        ):
            return
        if (
            "highway" not in tags
            and tags.get("railway") != "platform"
            and (
                tags.get("public_transport") != "platform"
                and tags.get("indoor") != "corridor"
            )
        ):
            return
        accepted = (
            is_walkable_way(tags)
            or is_linear_station_walkway(tags, refs)
            or allow_station_private_way(way.id, tags, self.allow_private_ways)
        )
        components = sorted(
            {
                component
                for ref in present
                for component in self.components.get(self.nodes.coords[ref], ())
            }
        )
        entry = {
            "id": way.id,
            "refs": present,
            "tags": tags,
            "accepted_by_filter": accepted,
            "components": components,
        }
        self.ways.append(entry)
        for ref in present:
            self.node_ways[ref].append(len(self.ways) - 1)


def local_station_access_diagnostic(
    index, walking, source, name, radius, allow_private_station_ways=False
):
    """Find topological gaps and nearby raw OSM ways; never repair by proximity."""
    if osmium is None:
        raise RuntimeError("Install osmium for station diagnosis: pip install osmium")
    rail_modes = {"mcc", "mcd", "train", "metro"}
    targets = [
        (i, stop)
        for i, stop in enumerate(index["stops"])
        if name.casefold() in (stop.get("name") or "").casefold()
        and rail_modes.intersection(stop.get("modes", ()))
    ]
    if not targets:
        raise ValueError("No rail stops matching station name: " + name)
    cx = sum(stop["x"] for _, stop in targets) / len(targets)
    cy = sum(stop["y"] for _, stop in targets) / len(targets)
    print("[access] Computing connected components...", flush=True)
    parent, sizes = walk_weak_components(walking)
    station_roots = {
        i: (
            parent[stop["walk_node"]]
            if 0 <= stop.get("walk_node", -1) < len(parent)
            else None
        )
        for i, stop in targets
    }
    target_roots = {root for root in station_roots.values() if root is not None}
    nearby_stops = []
    street_roots = set()
    for i, stop in enumerate(index["stops"]):
        distance = math.hypot(stop["x"] - cx, stop["y"] - cy)
        if distance > radius:
            continue
        node = stop.get("walk_node", -1)
        root = parent[node] if 0 <= node < len(parent) else None
        if root is not None and set(stop.get("modes", ())) & SURFACE_MODES:
            street_roots.add(root)
        if distance <= min(radius, 650.0):
            nearby_stops.append(
                {
                    "index": i,
                    "id": stop["id"],
                    "name": stop.get("name"),
                    "meters": round(distance),
                    "modes": stop.get("modes", ()),
                    "walk_node": node,
                    "component": root,
                    "lon": stop["lon"],
                    "lat": stop["lat"],
                    "component_nodes": sizes.get(root, 0),
                }
            )
    nearby_stops.sort(key=lambda item: item["meters"])
    # A street component is a component with at least one nearby surface stop.
    # Include large nearby components when no surface stops are available.
    coordinate_components = defaultdict(set)
    graph_local = []
    for i, (x, y) in enumerate(walking["nodes"]):
        if abs(x - cx) > radius + 80 or abs(y - cy) > radius + 80:
            continue
        if (x - cx) ** 2 + (y - cy) ** 2 > (radius + 80) ** 2:
            continue
        root = parent[i]
        coordinate_components[(round(x, 1), round(y, 1))].add(root)
        graph_local.append((i, x, y, root))
    del parent
    if not street_roots:
        candidates = [root for _, _, _, root in graph_local if root not in target_roots]
        if candidates:
            street_roots.add(max(candidates, key=lambda root: sizes[root]))
    print("[access] Reading local OSM nodes and ways...", flush=True)
    nodes = LocalStationNodes(index["origin"], cx, cy, radius)
    nodes.apply_file(str(source))
    ways = LocalStationWays(nodes, coordinate_components, allow_private_station_ways)
    ways.apply_file(str(source))
    coordinate_to_ids = defaultdict(list)
    for ref, xy in nodes.coords.items():
        coordinate_to_ids[xy].append(ref)

    # Search only graph nodes within the local circle, not every OSM way pair.
    cell_m = 15.0
    city_grid = defaultdict(list)
    platform = []
    for node, x, y, root in graph_local:
        if root in target_roots:
            platform.append((node, x, y, root))
        if root in street_roots and root not in target_roots:
            city_grid[(int(x // cell_m), int(y // cell_m))].append((node, x, y, root))
    gaps = []
    for node, x, y, component in platform:
        gx, gy = int(x // cell_m), int(y // cell_m)
        nearby = []
        for dx in range(-10, 11):
            for dy in range(-10, 11):
                for other, ox, oy, other_root in city_grid.get((gx + dx, gy + dy), ()):
                    distance = math.hypot(x - ox, y - oy)
                    if distance <= 140:
                        nearby.append((distance, other, ox, oy, other_root))
        for distance, other, ox, oy, other_root in sorted(nearby)[:3]:
            a_refs = coordinate_to_ids.get((round(x, 1), round(y, 1)), [])
            b_refs = coordinate_to_ids.get((round(ox, 1), round(oy, 1)), [])
            gaps.append(
                {
                    "gap_m": round(distance, 1),
                    "platform_graph_node": node,
                    "street_graph_node": other,
                    "platform_component": component,
                    "street_component": other_root,
                    "platform_osm_nodes": a_refs[:5],
                    "street_osm_nodes": b_refs[:5],
                    "from_xy": [x, y],
                    "to_xy": [ox, oy],
                }
            )
    gaps.sort(key=lambda item: item["gap_m"])
    gap_selected = []
    seen = set()
    for gap in gaps:
        key = (
            (
                gap["platform_osm_nodes"][0]
                if gap["platform_osm_nodes"]
                else gap["platform_graph_node"]
            ),
            gap["street_component"],
        )
        if key in seen:
            continue
        seen.add(key)
        gap_selected.append(gap)
        if len(gap_selected) >= 35:
            break

    for gap in gap_selected:
        for prefix in ("platform", "street"):
            linked = set()
            for ref in gap[prefix + "_osm_nodes"]:
                linked.update(ways.node_ways.get(ref, ()))
            gap[prefix + "_ways"] = [
                {
                    "id": ways.ways[i]["id"],
                    "tags": ways.ways[i]["tags"],
                    "accepted": ways.ways[i]["accepted_by_filter"],
                }
                for i in sorted(linked)[:8]
            ]

    relevant_ways = []
    for way in ways.ways:
        kinds = set(way["components"])
        suspicious = bool(kinds & target_roots and kinds & street_roots)
        touches_platform = bool(kinds & target_roots)
        special = (
            way["tags"].get("highway")
            in {
                "steps",
                "footway",
                "path",
                "elevator",
            }
            or way["tags"].get("indoor") == "corridor"
        )
        infrastructure = (
            way["tags"].get("bridge") == "yes"
            or way["tags"].get("highway") == "steps"
            or way["tags"].get("tunnel") == "yes"
        )
        if (
            suspicious
            or (not way["accepted_by_filter"] and (touches_platform or special))
            or (touches_platform and infrastructure)
        ):
            relevant_ways.append(
                {
                    "id": way["id"],
                    "tags": way["tags"],
                    "accepted": way["accepted_by_filter"],
                    "touches_platform": touches_platform,
                    "touches_street": bool(kinds & street_roots),
                    "osm_node_ids": way["refs"][:50],
                }
            )
    relevant_ways.sort(
        key=lambda item: (
            not (item["touches_platform"] and item["touches_street"]),
            not item["touches_platform"],
            item["accepted"],
        )
    )
    # Retain potential entrances and stairs even if no highway way links them.
    access_nodes = []
    for osm_id, tags in nodes.tags.items():
        xy = nodes.coords[osm_id]
        if math.hypot(xy[0] - cx, xy[1] - cy) > radius:
            continue
        if (
            tags.get("entrance")
            or tags.get("highway") in {"elevator", "steps"}
            or tags.get("railway") in {"subway_entrance", "station", "halt"}
        ):
            access_nodes.append(
                {
                    "osm_node": osm_id,
                    "xy": xy,
                    "tags": tags,
                    "components": sorted(coordinate_components.get(xy, ())),
                }
            )
    access_nodes.sort(
        key=lambda item: math.hypot(item["xy"][0] - cx, item["xy"][1] - cy)
    )
    # Limit detail but retain all nearby geometry in GeoJSON.
    closest_nodes = []
    for node, x, y, component in platform[:500]:
        closest_nodes.append(
            {
                "graph_node": node,
                "osm_ids": coordinate_to_ids.get((x, y), [])[:5],
                "xy": [x, y],
                "component": component,
            }
        )
    summary = {
        "station": name,
        "radius_m": radius,
        "center_xy": [round(cx, 1), round(cy, 1)],
        "station_stops": [
            {
                "index": i,
                "id": stop["id"],
                "name": stop.get("name"),
                "walk_node": stop.get("walk_node", -1),
                "component": (station_roots[i]),
            }
            for i, stop in targets
        ],
        "platform_components": [
            {"root": root, "nodes": sizes[root]} for root in sorted(target_roots)
        ],
        "street_components": [
            {"root": root, "nodes": sizes[root]} for root in sorted(street_roots)
        ],
        "nearby_stops": nearby_stops[:60],
        "platform_graph_nodes": closest_nodes,
        "nearby_osm_nodes": len(nodes.coords),
        "nearby_osm_ways": len(ways.ways),
        "access_nodes": access_nodes[:150],
        "nearest_gaps": gap_selected,
        "rejected_or_bridge_ways": relevant_ways[:150],
        "notes": [
            "Component IDs are diagnostic only and can change on rebuild.",
            "Components use fully resolved disjoint-set roots.",
            "Nearest gaps are geometrical candidates, NOT verified passages.",
            "A crossing on the map does not establish a shared OSM node.",
            "No graph links have been added by this diagnostic.",
        ],
    }
    return summary, station_access_geojson(
        summary, ways.ways, nodes, target_roots, street_roots, index["origin"]
    )


def station_access_geojson(summary, ways, nodes, platform_roots, street_roots, origin):
    """View suspect OSM topology and gaps in geojson.io or QGIS."""
    lon0, lat0 = origin
    lat_factor = 180.0 / (math.pi * EARTH_RADIUS_M)
    lon_factor = lat_factor / math.cos(math.radians(lat0))

    def ll(xy):
        return [
            round(lon0 + xy[0] * lon_factor, 7),
            round(lat0 + xy[1] * lat_factor, 7),
        ]

    features = []
    for way in ways:
        points = [ll(nodes.coords[ref]) for ref in way["refs"]]
        if len(points) < 2:
            continue
        roots = set(way["components"])
        if roots & platform_roots:
            category = "platform_component"
        elif roots & street_roots:
            category = "street_component"
        elif not way["accepted_by_filter"]:
            category = "rejected_by_filter"
        else:
            category = "other_walkway"
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": points,
                },
                "properties": {
                    "category": category,
                    "osm_way": way["id"],
                    "accepted": way["accepted_by_filter"],
                    "tags": way["tags"],
                },
            }
        )
    for gap in summary["nearest_gaps"]:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        ll(gap["from_xy"]),
                        ll(gap["to_xy"]),
                    ],
                },
                "properties": {
                    "category": "unverified_gap",
                    "gap_m": gap["gap_m"],
                    "platform_osm_nodes": gap["platform_osm_nodes"],
                    "street_osm_nodes": gap["street_osm_nodes"],
                },
            }
        )
    for stop in summary["nearby_stops"]:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [stop["lon"], stop["lat"]],
                },
                "properties": {"category": "nearby_stop", **stop},
            }
        )
    for node in summary["access_nodes"]:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": ll(node["xy"]),
                },
                "properties": {"category": "access_node", **node},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def write_station_access_diagnostic(args, index, walking, source):
    if args.walking_json:
        raise ValueError("Station access audit requires the original OSM PBF")
    report, geometry = local_station_access_diagnostic(
        index,
        walking,
        source,
        args.diagnose_station,
        args.diagnose_radius,
        allow_private_station_ways=args.allow_moskvorechye_private,
    )
    Path(args.diagnose_output).write_text(
        json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="ascii"
    )
    Path(args.diagnose_geojson).write_text(
        json.dumps(geometry, ensure_ascii=True) + "\n", encoding="ascii"
    )
    print("[access] Station:", report["station"], flush=True)
    for entry in report["platform_components"]:
        print(
            "[access] Platform component:",
            entry["root"],
            "nodes:",
            entry["nodes"],
            flush=True,
        )
    for entry in report["street_components"][:5]:
        print(
            "[access] Street component:",
            entry["root"],
            "nodes:",
            entry["nodes"],
            flush=True,
        )
    print("[access] Nearest candidate gaps (UNVERIFIED):", flush=True)
    for item in report["nearest_gaps"][:12]:
        print(
            "[access]   gap:",
            item["gap_m"],
            "m, OSM nodes:",
            item["platform_osm_nodes"],
            "->",
            item["street_osm_nodes"],
            flush=True,
        )
    print("[access] Nearby entrance/stair nodes:", flush=True)
    for item in report["access_nodes"][:15]:
        print("[access]   node:", item["osm_node"], item["tags"], flush=True)
    print("[access] Suspect OSM ways:", flush=True)
    for item in report["rejected_or_bridge_ways"][:20]:
        print(
            "[access]   way:",
            item["id"],
            "accepted:",
            item["accepted"],
            item["tags"],
            flush=True,
        )
    print("[access] JSON:", args.diagnose_output, flush=True)
    print("[access] GeoJSON:", args.diagnose_geojson, flush=True)
    return report


def audit_butovo_line(data):
    """Build-time rail line 12 presence and station-attachment smoke check."""
    result = []
    for key, route in data["routes"].items():
        if route.get("mode") != "metro":
            continue
        ref = str(route.get("ref") or "").strip()
        if ref != "12" and "butov" not in key.lower():
            continue
        found = {}
        for direction in route["directions"]:
            for idx in direction["stops"]:
                stop = data["stops"][idx]
                found[stop["id"]] = {
                    "id": stop["id"],
                    "name": stop.get("name"),
                    "walk_node": stop.get("walk_node", -1),
                }
        result.append(
            {
                "key": key,
                "ref": ref,
                "directions": len(route["directions"]),
                "stops": list(found.values()),
                "attached": sum(s["walk_node"] >= 0 for s in found.values()),
            }
        )
    count = sum(len(entry["stops"]) for entry in result)
    print("[metro-12] route keys:", len(result), "stop references:", count)
    for entry in result:
        print(
            "[metro-12]",
            entry["key"],
            "directions:",
            entry["directions"],
            "walk nodes:",
            entry["attached"],
            "/",
            len(entry["stops"]),
        )
    if not result:
        print("[metro-12] WARNING: Butovskaya line not found in the route index")
    return result


def audit_named_interchanges(data):
    """Inspect transfers in both directions at known Metro line 12 hubs."""
    pairs = [
        (
            "\u041d\u043e\u0432\u043e\u044f\u0441\u0435\u043d"
            "\u0435\u0432\u0441\u043a\u0430\u044f",
            "\u0411\u0438\u0442\u0446\u0435\u0432\u0441\u043a"
            "\u0438\u0439 \u043f\u0430\u0440\u043a",
        ),
        (
            "\u0411\u0443\u043b\u044c\u0432\u0430\u0440 "
            "\u0414\u043c\u0438\u0442\u0440\u0438\u044f "
            "\u0414\u043e\u043d\u0441\u043a\u043e\u0433\u043e",
            "\u0423\u043b\u0438\u0446\u0430 \u0421\u0442"
            "\u0430\u0440\u043e\u043a\u0430\u0447\u0430\u043b"
            "\u043e\u0432\u0441\u043a\u0430\u044f",
        ),
    ]
    stops = data["stops"]
    transfers = data["walk_transfers"]
    result = []
    for left, right in pairs:
        left_idxs = [
            i
            for i, stop in enumerate(stops)
            if left.casefold() in (stop.get("name") or "").casefold()
            and "metro" in stop.get("modes", ())
        ]
        right_idxs = [
            i
            for i, stop in enumerate(stops)
            if right.casefold() in (stop.get("name") or "").casefold()
            and "metro" in stop.get("modes", ())
        ]
        arcs = []
        for from_idxs, to_idxs, direction in (
            (left_idxs, right_idxs, "forward"),
            (right_idxs, left_idxs, "reverse"),
        ):
            to_set = set(to_idxs)
            for source in from_idxs:
                first, last = transfers["offsets"][source : source + 2]
                for pos in range(first, last):
                    target = transfers["to"][pos]
                    if target in to_set:
                        arcs.append(
                            {
                                "from_id": stops[source]["id"],
                                "to_id": stops[target]["id"],
                                "meters": transfers["meters"][pos],
                                "direction": direction,
                            }
                        )
        summary = {
            "left": left,
            "right": right,
            "left_stops": [stops[i]["id"] for i in left_idxs],
            "right_stops": [stops[i]["id"] for i in right_idxs],
            "directed_transfers": arcs[:40],
        }
        result.append(summary)
        print(
            "[interchange]",
            left,
            "<->",
            right,
            "rail stops:",
            len(left_idxs),
            "/",
            len(right_idxs),
            "directed arcs:",
            len(arcs),
        )
        if not arcs:
            print("[interchange] WARNING: no directed route through this interchange")
    return result


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
                    suspicious.append(
                        {
                            "route": key,
                            "from": stops[a]["name"],
                            "to": stops[b]["name"],
                            "distance_m": round(distance),
                        }
                    )

    shabolovskaya = "\u0428\u0430\u0431\u043e\u043b\u043e\u0432\u0441\u043a\u0430\u044f"
    station_matches = [
        stop["name"]
        for stop in stops
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


# Standalone HTML/CSS/JavaScript source

# Web UI lives in the web/ source tree. No JavaScript/CSS is embedded in Python.
WEB_SOURCE_DIR = Path(__file__).resolve().parent / "web"


# Pack 0.1 m coordinates and edge lengths into 32-bit integers. This
# changes only the HTML payload; build caches retain the readable graph.
# A 2.3M-node graph otherwise generates tens of millions of JSON digits.


def encode_unsigned_varint(output, value):
    if value < 0 or value >= 2**32:
        raise ValueError("Walking graph value exceeds unsigned 32-bit range")
    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)


def encode_signed_varint(output, value):
    if not -(2**31) <= value < 2**31:
        raise ValueError("Walking graph coordinate/delta exceeds 32 bits")
    encode_unsigned_varint(output, (value << 1) ^ (value >> 31))


def pack_walking_payload(data):
    walking = data["walking"]
    vertices = bytearray()
    arcs = bytearray()
    previous_x = 0
    previous_y = 0
    for x, y in walking["nodes"]:
        next_x = round(x * 10)
        next_y = round(y * 10)
        encode_signed_varint(vertices, next_x - previous_x)
        encode_signed_varint(vertices, next_y - previous_y)
        previous_x, previous_y = next_x, next_y
    previous_a = 0
    for a, b, meters, direction in walking["edges"]:
        encode_signed_varint(arcs, a - previous_a)
        encode_signed_varint(arcs, b - a)
        encode_unsigned_varint(arcs, round(meters * 10))
        arcs.append(direction)
        previous_a = a
    encoded = {
        "format": "delta-varint-dm-v1",
        "node_count": len(walking["nodes"]),
        "edge_count": len(walking["edges"]),
        "nodes": base64.b64encode(vertices).decode("ascii"),
        "edges": base64.b64encode(arcs).decode("ascii"),
    }
    return {**data, "walking": encoded}


def pack_transfers_payload(graph):
    """Encode CSR transfers without changing the cached graph or distances."""
    offsets = graph["offsets"]
    targets = graph["to"]
    meters = graph["meters"]
    if len(offsets) < 1 or offsets[0] != 0:
        raise ValueError("Transfer offsets must begin at zero")
    if offsets[-1] != len(targets) or len(targets) != len(meters):
        raise ValueError("Transfer CSR arrays have inconsistent lengths")
    out = bytearray()
    stop_count = len(offsets) - 1
    for source in range(stop_count):
        first, last = offsets[source : source + 2]
        if not 0 <= first <= last <= len(targets):
            raise ValueError("Transfer CSR offsets are not sorted")
        encode_unsigned_varint(out, last - first)
        previous = 0
        for index in range(first, last):
            target = targets[index]
            if not previous <= target < stop_count:
                raise ValueError("Transfer targets must be sorted by stop")
            encode_unsigned_varint(out, target - previous)
            previous = target
            distance = meters[index]
            centimeters = round(distance * 100)
            if abs(centimeters / 100 - distance) > 0.0000001:
                raise ValueError("Transfer distance would lose precision")
            encode_unsigned_varint(out, centimeters)
    return {
        "format": "csr-delta-varint-cm-v1",
        "stop_count": stop_count,
        "arc_count": len(targets),
        "arcs": base64.b64encode(out).decode("ascii"),
    }


def pack_route_geometry_payload(data):
    """Pack line geometry to 1e-7 degrees, about a centimeter at Moscow."""
    packed_routes = {}
    encoded = bytearray()
    count = 0
    for key, route in data["routes"].items():
        compact_route = dict(route)
        compact_route.pop("stops", None)
        compact_route.pop("network", None)
        compact_route.pop("roundtrip", None)
        directions = []
        for direction in route["directions"]:
            compact_direction = dict(direction)
            compact_direction.pop("geometry_source", None)
            # Keep roundtrip=yes! The browser needs it for topology-based
            # circle continuation; the old compact payload dropped it.
            geometry = direction.get("geometry") or {}
            kind = geometry.get("type")
            if kind in ("LineString", "MultiLineString"):
                parts = (
                    [geometry["coordinates"]]
                    if kind == "LineString"
                    else geometry["coordinates"]
                )
                encoded.append(1 if kind == "LineString" else 2)
                encode_unsigned_varint(encoded, len(parts))
                for line in parts:
                    encode_unsigned_varint(encoded, len(line))
                    previous_lon = 0
                    previous_lat = 0
                    for lon, lat in line:
                        next_lon = round(lon * 10000000)
                        next_lat = round(lat * 10000000)
                        encode_signed_varint(encoded, next_lon - previous_lon)
                        encode_signed_varint(encoded, next_lat - previous_lat)
                        previous_lon, previous_lat = next_lon, next_lat
                compact_direction["geometry"] = count
                count += 1
            directions.append(compact_direction)
        compact_route["directions"] = directions
        packed_routes[key] = compact_route
    return packed_routes, {
        "format": "route-delta-varint-e7-v1",
        "count": count,
        "data": base64.b64encode(encoded).decode("ascii"),
    }


def pack_html_payload(data):
    """Strip build-only indexes and encode routes, walking, and transfers."""
    routes, geometries = pack_route_geometry_payload(data)
    trimmed = {
        **data,
        "routes": routes,
        "route_geometries": geometries,
        "walk_transfers": pack_transfers_payload(data["walk_transfers"]),
    }
    trimmed.pop("grid", None)
    trimmed.pop("grid_size", None)
    return pack_walking_payload(trimmed)


# Static page generator (HTML + CSS + JS + worker + compressed payload)


def write_atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_html(
    data,
    initial_walk_minutes,
    walk_speed,
    initial_circle_opacity,
    output_path,
    reuse_data=False,
):
    if not reuse_data:
        html_started = time.monotonic()
        print("[build] START JSON serialization", flush=True)
        raw_payload = json.dumps(
            pack_html_payload(data),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        print(
            "[build] DONE JSON serialization:",
            f"{time.monotonic()-html_started:.3f}s",
            flush=True,
        )
        html_started = time.monotonic()
        print("[build] START gzip compression", flush=True)
        compressed_payload = gzip.compress(raw_payload, compresslevel=9, mtime=0)
        print(
            "[build] DONE gzip compression:",
            f"{time.monotonic()-html_started:.3f}s",
            flush=True,
        )
        print(
            "external data:",
            f"{len(raw_payload) / (1024 * 1024):.1f} MiB raw ->",
            f"{len(compressed_payload) / (1024 * 1024):.1f} MiB gzip",
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

    # Render static interface labels as plain ASCII text. OSM place names
    # remain untouched inside the compressed transport payload.
    mode_names = {
        "heading": "Transport modes",
        "bus": "Bus",
        "tram": "Tram",
        "trolleybus": "Trolleybus",
        "metro": "Metro",
        "mcc": "MCC",
        "rail": "MCD + Trains",
    }
    mode_hint = "Surface transport on by default. Travel times are estimates."

    output_path = Path(output_path)
    base = output_path.stem
    data_filename = base + ".json.gz"
    js_filename = base + ".js"
    css_filename = base + ".css"
    data_worker_filename = base + "-data-worker.js"
    maplibre_worker_filename = base + "-maplibre-worker.js"
    config_filename = base + "-config.json"
    html = (WEB_SOURCE_DIR / "page.html").read_text(encoding="ascii")
    from boot_svg import render_inline_svg

    boot_svg = render_inline_svg(output_path.with_name("moscow-major-roads.json"))
    boot_css = (WEB_SOURCE_DIR / "boot-loader.css").read_text(encoding="ascii")

    # Python injects static road DATA and critical CSS, not source JS code.
    config = {
        "version": PROJECT_VERSION,
        "data_file": data_filename,
        "data_worker_file": data_worker_filename,
        "maplibre_worker_file": maplibre_worker_filename,
        "walk_speed": walk_speed,
        "grid_size": grid_size,
        "origin_lon": origin_lon,
        "origin_lat": origin_lat,
        "mode_names": mode_names,
        "mode_hint": mode_hint,
        "infer_through_service": data.get("through_service_policy") == "estimated",
        "initial_walk_minutes": initial_walk_minutes,
        "initial_opacity": initial_circle_opacity,
    }
    replacements = {
        "@@VERSION@@": PROJECT_VERSION,
        "@@PAGE_JS@@": quote(js_filename),
        "@@PAGE_CSS@@": quote(css_filename),
        "@@CONFIG_JSON@@": quote(config_filename),
        "@@BOOT_ROADS_SVG@@": boot_svg,
        "@@BOOT_CSS@@": boot_css,
        "@@INITIAL_WALK_MINUTES@@": f"{initial_walk_minutes:.0f}",
        "@@INITIAL_OPACITY@@": f"{initial_circle_opacity:.0f}",
        "@@WALK_SPEED@@": f"{walk_speed:.0f}",
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    if re.search(r"@@[A-Z_]+@@", html):
        raise ValueError("Unresolved HTML template placeholder")
    if not html.isascii():
        raise ValueError("Generated HTML must contain ASCII only")
    result = (
        "\n".join(line.rstrip() for line in html.splitlines() if line.strip()) + "\n"
    )
    long_lines = [i for i, line in enumerate(result.splitlines(), 1) if len(line) > 100]
    if long_lines:
        raise ValueError(f"Generated HTML lines exceed 100: {long_lines[:10]}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    icon_assets = Path(__file__).resolve().parent / "assets"
    for name in (
        "favicon.svg",
        "favicon.ico",
        "favicon-32.png",
        "apple-touch-icon.png",
    ):
        write_atomic_bytes(
            output_path.with_name(name), (icon_assets / name).read_bytes()
        )
    if not reuse_data:
        write_atomic_bytes(output_path.with_name(data_filename), compressed_payload)
    write_atomic_bytes(
        output_path.with_name(config_filename),
        (json.dumps(config, ensure_ascii=True, indent=2) + "\n").encode("ascii"),
    )
    static_assets = {
        js_filename: "routes.js",
        css_filename: "routes.css",
        data_worker_filename: "routes-data-worker.js",
        "boot-loader.js": "boot-loader.js",
    }
    for destination, source in static_assets.items():
        content = (WEB_SOURCE_DIR / source).read_bytes()
        write_atomic_bytes(output_path.with_name(destination), content)
    # Older releases copied this stylesheet, but it is now inlined for FCP.
    output_path.with_name("boot-loader.css").unlink(missing_ok=True)
    # Prefer to vendor the official matching CSP worker once at build time.
    # If unavailable, fall back to a same-origin shim importing from the
    # same unpkg origin that already serves MapLibre's main JS bundle.
    csp_worker_path = output_path.with_name(maplibre_worker_filename)
    if not csp_worker_path.is_file() or csp_worker_path.stat().st_size < 100000:
        url = "https://unpkg.com/maplibre-gl@4.7.1/dist/" "maplibre-gl-csp-worker.js"
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "transport-map-static-builder"}
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                content = response.read(2 * 1024 * 1024 + 1)
            if not 100000 < len(content) < 2 * 1024 * 1024:
                raise ValueError("Unexpected MapLibre CSP worker size")
            write_atomic_bytes(csp_worker_path, content)
            print("[build] vendored MapLibre 4.7.1 CSP worker", flush=True)
        except (OSError, ValueError, urllib.error.URLError) as error:
            print("[build] MapLibre worker download unavailable:", error)
            print("[build] using CDN-backed same-origin shim", flush=True)
            maplibre_shim = (
                "// Same-origin MapLibre CSP Worker for worker-src 'self'.\n"
                'importScripts("https://unpkg.com/maplibre-gl@4.7.1/'
                'dist/maplibre-gl-csp-worker.js");\n'
            )
            write_atomic_bytes(csp_worker_path, maplibre_shim.encode("ascii"))
    print(
        "[build] web assets:",
        ", ".join(
            (
                data_filename,
                js_filename,
                css_filename,
                data_worker_filename,
                maplibre_worker_filename,
            )
        ),
        flush=True,
    )
    return result


# CLI

#!/usr/bin/env python3


sys.dont_write_bytecode = True


def test_surface_route_pairing():
    """Topology regression: no numeric or named-route special cases."""

    def point(idx, off=0):
        return {
            "id": f"n{idx}_{off}",
            "lon": 37.0 + idx * 0.005,
            "lat": 55.0 + off * 0.00005,
            "name": f"Stop {idx}",
        }

    items = [point(i) for i in range(6)] + [point(i, 1) for i in range(6)]
    stop_index = {item["id"]: i for i, item in enumerate(items)}

    def feat(ref, relation, ids, mode="bus", key=None):
        return {
            "type": "Feature",
            "properties": {
                "feature_type": "route",
                "mode": mode,
                "ref": ref,
                "network": "Example Transit",
                "relation_id": relation,
                "route_key": key or f"{mode}:relation:{relation}",
                "stops": ids,
            },
        }

    forward = [f"n{i}_0" for i in range(6)]
    backward = [f"n{i}_1" for i in range(5, -1, -1)]
    pair = [feat("ABC", 111, forward), feat("ABC", 222, backward)]
    paired = pair_surface_route_directions(pair, stop_index, items)
    assert len(paired) == 1
    assert pair[0]["properties"]["route_key"] == pair[1]["properties"]["route_key"]
    assert pair[0]["properties"]["route_key"].startswith("bus:paired:")
    # Identical route number alone is not evidence of pairing.
    wrong = [feat("ABC", 111, forward), feat("OTHER", 222, backward)]
    assert not pair_surface_route_directions(wrong, stop_index, items)
    # Rail variants must never be paired by this surface approximation.
    train = [feat("ABC", 111, forward, "train"), feat("ABC", 222, backward, "train")]
    assert not pair_surface_route_directions(train, stop_index, items)
    # Opposite termini, but no shared corridor.
    detour = [
        feat("ABC", 111, forward),
        feat("ABC", 222, [backward[0], backward[1], "n5_1", "n5_1", backward[-1]]),
    ]
    assert not pair_surface_route_directions(detour, stop_index, items)
    # Multiple equally plausible branches: never guess a continuation.
    ambiguous = [
        feat("ABC", 111, forward),
        feat("ABC", 222, backward),
        feat("ABC", 333, backward),
    ]
    assert not pair_surface_route_directions(ambiguous, stop_index, items)
    print(
        "[test] PASS: universal reciprocal pairing, unique endpoints, corridor guards"
    )


def run_self_tests():
    test_surface_route_pairing()
    """Offline regressions for stations, rail identity, and transfers."""
    page_html = (WEB_SOURCE_DIR / "page.html").read_text(encoding="ascii")
    routes_js = (WEB_SOURCE_DIR / "routes.js").read_text(encoding="ascii")
    transfer_input = re.search(
        r'<input\s+id="transfer-count"(?P<attrs>.*?)>', page_html, re.S
    )
    assert transfer_input is not None
    assert re.search(r'\bvalue="0"', transfer_input.group("attrs"))
    assert re.search(r'\bmax="2"', transfer_input.group("attrs"))
    assert '<b id="transfer-mode-value">No transfers</b>' in page_html
    assert page_html.index('id="trip-back-top"') < page_html.index('id="trip-steps"')
    assert page_html.index('id="trip-back"') > page_html.index('id="trip-steps"')
    assert 'tripBackTop.addEventListener("click", backToReachability)' in routes_js
    assert 'tripBack.addEventListener("click", backToReachability)' in routes_js
    assert not re.search(r"\\u[0-9A-Fa-f]{4}", routes_js)
    assert "bus: transportConfig.mode_names.bus" in routes_js
    assert "metro: transportConfig.mode_names.metro" in routes_js
    assert not re.search(r"&#[0-9]+;", page_html)
    # A bad route_master must not turn a bus into a train or vice versa.
    parent_train = {
        "train:master:9": {"relation_id": 9, "route": "train", "mode": "train"}
    }
    bus = {"route": "bus", "mode": "bus", "ref": "356k"}
    assert resolve_route_mode_and_master(bus, "train:master:9", parent_train) == (
        "bus",
        None,
        True,
    )
    parent_bus = {"bus:master:10": {"relation_id": 10, "route": "bus", "mode": "bus"}}
    rail = {"route": "train", "mode": "train", "ref": "A"}
    assert resolve_route_mode_and_master(rail, "bus:master:10", parent_bus) == (
        "train",
        None,
        True,
    )
    mcd_master = {
        "train:master:11": {"relation_id": 11, "route": "train", "mode": "mcd"}
    }
    assert resolve_route_mode_and_master(rail, "train:master:11", mcd_master) == (
        "mcd",
        "train:master:11",
        False,
    )
    assert resolve_route_mode_and_master(bus, None, {}) == ("bus", None, False)
    # A numbered railway is not necessarily MCC.
    assert transport_mode({"ref": "14"}, "train") == "train"
    assert transport_mode({"ref": "14", "network": "MCC"}, "train") == "mcc"
    assert transport_mode({"ref": "D2"}, "train") == "mcd"
    assert transport_mode({"name": "MCC"}, "subway") == "metro"
    assert transport_mode({"ref": "356k"}, "bus") == "bus"
    assert normalized_route_ref(" 356\u043a ") == normalized_route_ref("356K")
    fake_feature = {
        "type": "FeatureCollection",
        "features": [
            {
                "properties": {
                    "feature_type": "route",
                    "ref": "356k",
                    "mode": "bus",
                    "route": "bus",
                    "relation_id": 123,
                }
            }
        ],
    }
    assert len(inspect_route_ref(fake_feature, "356\u043a")["route_directions"]) == 1
    assert resolve_route_mode_and_master(
        {"route": "subway", "mode": "metro"}, "train:master:11", mcd_master
    ) == ("metro", None, True)
    # Explicit long-distance exclusion cannot be bypassed by a rail master.
    assert resolve_route_mode_and_master(
        {"route": "train", "mode": None}, "train:master:11", mcd_master
    ) == (None, "train:master:11", False)
    road_way_tags = {
        1: {"highway": "residential", "railway": None},
        2: {"highway": "primary", "railway": None},
        3: {"highway": None, "railway": "rail"},
    }
    false_train = {"route": "train", "way_members": [(1, ""), (2, "")]}
    assert (
        incompatible_route_way_evidence(false_train, road_way_tags)["reason"]
        == "rail_service_mapped_on_roads"
    )
    true_train = {"route": "train", "way_members": [(1, ""), (2, ""), (3, "")]}
    assert incompatible_route_way_evidence(true_train, road_way_tags) is None
    assert (
        incompatible_route_way_evidence(
            {"route": "train", "way_members": []}, road_way_tags
        )
        is None
    )
    assert (
        incompatible_route_way_evidence(
            {"route": "bus", "way_members": [(1, ""), (2, "")]}, road_way_tags
        )
        is None
    )
    assert route_identity("train", "A", 101) != route_identity("train", "A", 102)
    assert route_identity("train", "A", 101, "train:master:1") == (
        route_identity("train", "A", 102, "train:master:1")
    )
    assert route_identity("mcd", "D2", 100) == route_identity("mcd", "D2", 200)
    assert resolved_route_member_runs(
        ["a", "b", "c", "d", "e"],
        ["stop"] * 5,
        {"a", "b", "d", "e"},
        rail=True,
    ) == [[("a", "stop"), ("b", "stop")], [("d", "stop"), ("e", "stop")]]
    assert resolved_route_member_runs(
        ["a", "missing", "b", "c"],
        ["stop", "stop", "platform", "stop"],
        {"a", "b", "c"},
        rail=True,
    ) == [[("a", "stop"), ("b", "platform"), ("c", "stop")]]
    sample = [
        {
            "id": "n1",
            "name": "\u041f\u0430\u0432\u0435\u043b\u0435\u0446\u043a\u0430\u044f",
            "lat": 55.73,
            "lon": 37.64,
            "routes": ["train:relation:101"],
            "walk_node": 1,
            "modes": ["train"],
        },
        {
            "id": "n2",
            "name": "\u0411\u0438\u0440\u044e\u043b\u0451\u0432\u043e",
            "lat": 55.60,
            "lon": 37.66,
            "routes": ["train:relation:101"],
            "walk_node": -1,
            "modes": ["train"],
        },
    ]
    demo_index = {
        "stops": sample,
        "routes": {
            "train:relation:101": {
                "mode": "train",
                "ref": "A",
                "directions": [{"relation_id": 101, "stops": [0, 1]}],
            }
        },
    }
    demo_audit = audit_paveletsky_corridor(
        demo_index,
        {
            "extraction_audit": {
                "rail_routes_with_missing_stops": [
                    {"relation_id": 101, "missing": 2},
                ]
            },
        },
    )
    assert len(demo_audit["directions"]) == 1
    assert demo_audit["directions"][0]["unresolved_osm_members"] == 2
    assert demo_audit["directions"][0]["long_stop_gaps"]
    assert demo_audit["directions"][0]["missing_walk_nodes"]
    # A routed right angle is longer than the stop-to-stop chord.
    geom_stops = [
        {"lon": 37.000, "lat": 55.000},
        {"lon": 37.001, "lat": 55.001},
        {"lon": 37.002, "lat": 55.001},
    ]
    right_angle = {
        "type": "LineString",
        "coordinates": [
            [37.000, 55.000],
            [37.001, 55.000],
            [37.001, 55.001],
            [37.002, 55.001],
        ],
    }
    segments = conservative_geometry_edge_lengths(
        right_angle, [0, 1, 2], geom_stops, "bus"
    )
    assert segments[0] is not None and segments[1] is not None
    assert segments[0] > coord_distance_m((37.000, 55.000), (37.001, 55.001))
    assert conservative_geometry_edge_lengths(
        right_angle, [2, 1, 0], geom_stops, "bus"
    ) == [None, None]
    far_stops = [{"lon": 39.0, "lat": 56.0}, geom_stops[1]]
    assert conservative_geometry_edge_lengths(
        right_angle, [0, 1], far_stops, "bus"
    ) == [None]
    broken = {
        "type": "MultiLineString",
        "coordinates": [
            [[37.000, 55.000], [37.001, 55.000]],
            [[37.003, 55.000], [37.004, 55.000]],
        ],
    }
    assert conservative_geometry_edge_lengths(
        broken, [0, 1], [geom_stops[0], {"lon": 37.004, "lat": 55.000}], "bus"
    ) == [None]
    # Any truly mapped outdoor edge is valid, including a 1-edge approach.
    edges = [[0, 1, 10.0, 1], [2, 3, 10.0, 1]]
    parents, mapped, trusted, inferred, preferred = exterior_component_edges(
        4, edges, {0, 1}, {0}
    )
    assert node_in_exterior_component(0, parents, mapped)
    assert node_in_exterior_component(2, parents, mapped)
    assert not inferred and not preferred and not trusted

    # An entrance sharing an exact OSM node with that short outdoor walkway
    # must retain that node instead of snapping across a nearby street.
    entrance = [
        {
            "id": "n123",
            "entrance_id": 123,
            "x": 0.0,
            "y": 0.0,
            "modes": [],
        }
    ]
    graph, missed = attach_stops_to_walk_graph(
        [[0.0, 0.0], [10.0, 0.0]],
        [[0, 1, 10.0, 1]],
        entrance,
        osm_node_index={123: 0},
        exterior_parents=array("I", [0, 0]),
        exterior_roots={0},
    )
    assert not missed and entrance[0]["walk_node"] == 0
    assert len(graph["nodes"]) == 2

    # A real, isolated 1-edge exterior path must remain and also link
    # conservatively to a large known street segment when close enough.
    entrances = [
        {
            "id": "n123",
            "entrance_id": 123,
            "x": 0.0,
            "y": 0.0,
            "modes": [],
            "snap_limit": 35.0,
        }
    ]
    joined, errors = attach_stops_to_walk_graph(
        [[0.0, 0.0], [10.0, 0.0], [0.0, 16.0], [10.0, 16.0]],
        [[0, 1, 10.0, 1], [2, 3, 10.0, 1]],
        entrances,
        osm_node_index={123: 0},
        street_edges={1},
        exterior_parents=array("I", [0, 0, 2, 2]),
        exterior_roots={0, 2},
        trusted_exterior_roots={2},
    )
    assert not errors
    assert entrances[0]["walk_node"] == 0
    assert any(edge[3] == 5 and 0 in edge[:2] for edge in joined["edges"])

    # Station-specific indoor stairs do not become outdoor anchors.
    assert not is_exterior_walkway({"highway": "steps"})
    assert not is_exterior_walkway({"highway": "footway", "tunnel": "yes"})
    assert not is_walkable_way({"highway": "motorway"})
    assert not is_walkable_way({"highway": "primary", "foot": "no"})
    assert not is_walkable_way(
        {"highway": "secondary", "sidewalk": "no", "bridge": "yes"}
    )
    assert is_walkable_way({"highway": "tertiary"})
    assert is_walkable_way({"highway": "residential"})
    assert is_walkable_way({"highway": "footway", "bridge": "yes"})

    # A stop_area without mapped entrances still contributes valid
    # station identity to stop_area_group interchange resolution.
    unnamed = [
        {
            "id": "n1",
            "name": "A",
            "x": 0.0,
            "y": 0.0,
            "walk_node": -1,
            "modes": ["metro"],
        },
        {
            "id": "n2",
            "name": "B",
            "x": 100.0,
            "y": 0.0,
            "walk_node": -1,
            "modes": ["metro"],
        },
    ]
    collector = StationEntranceCollector(unnamed, (0.0, 0.0))
    collector.areas = [
        (10, "A", [("n1", "stop")]),
        (11, "B", [("n2", "stop")]),
    ]
    collector.area_groups = [(99, [("r10", ""), ("r11", "")])]
    collector.resolve(unnamed)
    assert collector.area_route_stops[10] == {"n1"}
    assert collector.area_route_stops[11] == {"n2"}
    assert len(station_interchange_hints(collector, unnamed)) == 2

    # Group relation grants a bounded, transfer-only estimate, never a
    # street edge or a universal teleport between similarly named stations.
    class Areas:
        pass

    areas = Areas()
    areas.area_groups = [(99, [("r10", ""), ("r11", "")])]
    areas.area_route_stops = {10: {"n1"}, 11: {"n2"}}
    stops = [
        {"id": "n1", "x": 0.0, "y": 0.0, "walk_node": 0},
        {"id": "n2", "x": 140.0, "y": 0.0, "walk_node": 1},
    ]
    hints = station_interchange_hints(areas, stops)
    assert len(hints) == 2
    assert hints[0] == [0, 1, 260.0, 99]
    assert hints[1] == [1, 0, 260.0, 99]
    stops[1]["walk_node"] = -1
    assert len(station_interchange_hints(areas, stops)) == 2
    stops[1]["walk_node"] = 1
    new_offsets, targets, meters = merge_interchange_hints(
        [0, 1, 2],
        [0, 1],
        [0.0, 0.0],
        hints,
        2,
    )
    assert new_offsets == [0, 2, 4]
    assert targets == [0, 1, 0, 1]
    assert meters == [0.0, 260.0, 260.0, 0.0]
    old_offsets, old_targets, old_meters = merge_interchange_hints(
        [0, 2, 3],
        [0, 1, 1],
        [0.0, 190.0, 0.0],
        hints,
        2,
    )
    assert old_offsets == [0, 2, 4]
    assert old_targets == [0, 1, 0, 1]
    assert old_meters == [0.0, 190.0, 260.0, 0.0]
    stops[1]["x"] = 500.0
    assert not station_interchange_hints(areas, stops)

    assert is_linear_station_walkway(
        {"railway": "platform", "highway": "footway"}, [1, 2]
    )
    assert is_linear_station_walkway(
        {"railway": "platform", "area": "yes", "highway": "footway"},
        [1, 2, 3, 1],
    )
    # PTv2 train: route uses stop_position on tracks but stop_area maps a
    # platform way connected via real OSM paths. No entrance object exists.
    rail_stop = [
        {
            "id": "n500",
            "name": "Rail A",
            "x": 0.0,
            "y": 0.0,
            "modes": ["train"],
            "walk_node": -1,
        }
    ]
    rail_areas = StationEntranceCollector(rail_stop, (0.0, 0.0))
    rail_areas.areas = [(55, "Rail A", [("n500", "stop"), ("w700", "platform")])]
    rail_areas.resolve(rail_stop, {"w700": "Rail A"}, {"w700": (15.0, 0.0)})
    assert rail_areas.platform_access["n500"] == {"w700": "platform"}
    outdoor = {
        "nodes": [[15.0, 0.0], [40.0, 0.0], [60.0, 0.0]],
        "edges": [[0, 1, 25.0, 1], [1, 2, 20.0, 1]],
    }
    exterior = array("I", [0, 0, 0])
    add_station_access_links(
        outdoor,
        rail_stop,
        rail_areas,
        [],
        osm_way_index={700: [0, 1]},
        exterior_parents=exterior,
        exterior_roots={0},
    )
    assert rail_stop[0]["walk_node"] == 3
    assert outdoor["edges"][-1][0] == 3
    assert outdoor["edges"][-1][1] in (0, 1)
    # An isolated station platform is not silently connected to a street.
    isolated = [
        {
            "id": "n500",
            "name": "Rail A",
            "x": 0.0,
            "y": 0.0,
            "modes": ["train"],
            "walk_node": -1,
        }
    ]
    isolated_graph = {"nodes": [[15.0, 0.0], [40.0, 0.0]], "edges": [[0, 1, 25.0, 1]]}
    add_station_access_links(
        isolated_graph,
        isolated,
        rail_areas,
        [],
        osm_way_index={700: [0, 1]},
        exterior_parents=array("I", [0, 0]),
        exterior_roots=set(),
    )
    assert isolated[0]["walk_node"] == -1
    assert len(isolated_graph["edges"]) == 1
    # OTP-style platform surface: a stair/footway ends inside a polygon,
    # sharing no boundary node, and a train stop sits on the adjacent rail.
    area_nodes = [
        [0.0, 0.0],
        [90.0, 0.0],
        [90.0, 12.0],
        [0.0, 12.0],
        [45.0, 6.0],
        [45.0, 30.0],
    ]
    area_edges = [
        [0, 1, 90.0, 11],
        [1, 2, 12.0, 11],
        [2, 3, 90.0, 11],
        [3, 0, 12.0, 11],
        [4, 5, 24.0, 1],
    ]
    polygon = {
        "id": 700,
        "ring": [0, 1, 2, 3],
        "level": None,
        "layer": "1",
        "railway": "platform",
    }
    platform_vertices = {700: [0, 1, 2, 3]}
    links = link_platform_surfaces(
        area_nodes,
        area_edges,
        [polygon],
        {4: (None, None, "footway")},
        platform_vertices,
    )
    assert links["connected_stubs"] == 1
    assert links["unknown_stub_level"] == 1
    assert 4 in platform_vertices[700]
    assert any(4 in edge[:2] and edge[3] == 11 for edge in area_edges)
    rail_stop = [
        {
            "id": "n800",
            "name": "Platform Example",
            "x": 45.0,
            "y": -3.0,
            "modes": ["train"],
            "walk_node": -1,
        }
    ]
    areas = StationEntranceCollector(rail_stop, (0.0, 0.0))
    matched = match_nearby_platform_polygons(areas, rail_stop, [polygon], area_nodes)
    assert matched["geometry_matched"] == 1
    assert areas.platform_access["n800"] == {"w700": "platform_geometry"}
    parents, mapped, *_ignored = exterior_component_edges(
        len(area_nodes), area_edges, {4}, {4}
    )
    example_graph = {"nodes": area_nodes, "edges": area_edges}
    add_station_access_links(
        example_graph,
        rail_stop,
        areas,
        [],
        osm_way_index=platform_vertices,
        exterior_parents=parents,
        exterior_roots=mapped,
    )
    assert rail_stop[0]["walk_node"] >= 0
    assert rail_stop[0]["station_access_kind"] == "platform_geometry_inferred"
    # An explicitly mismatched platform elevation cannot form a link.
    wrong_level = link_platform_surfaces(
        area_nodes[:],
        [edge[:] for edge in area_edges[:5]],
        [polygon],
        {4: (None, "-1", "footway")},
    )
    assert not wrong_level.get("connected_stubs", 0)
    assert wrong_level["different_levels"] == 1
    # Geometrically adjacent but ambiguous parallel platforms: no guess.
    alternate = {**polygon, "id": 701}
    ambiguous = StationEntranceCollector(rail_stop, (0.0, 0.0))
    result = match_nearby_platform_polygons(
        ambiguous, rail_stop, [polygon, alternate], area_nodes
    )
    assert result["ambiguous_platforms"] == 1
    assert not ambiguous.platform_access.get("n800")
    # A railway station point is usable as an estimated boarding anchor only
    # when it is explicitly named and close to the train stop. The anchor
    # must be snapped to a mapped public outdoor footway, never a road.
    point_stops = [
        {
            "id": "n801",
            "name": "Terminal A",
            "x": 0.0,
            "y": 0.0,
            "modes": ["train"],
            "walk_node": -1,
        },
        {
            "id": "n802",
            "name": "Terminal A",
            "x": 2.0,
            "y": 0.0,
            "modes": ["train"],
            "walk_node": -1,
        },
    ]
    facilities = StationEntranceCollector(point_stops, (0.0, 0.0))
    facilities.facility_nodes[803] = {
        "x": 1.0,
        "y": 1.0,
        "name": "Terminal A",
        "layer": None,
        "level": None,
    }
    matches = match_station_facility_anchors(facilities, point_stops)
    assert matches["matched_facility_stops"] == 2
    entry = [
        {
            "id": "n803",
            "entrance_id": 803,
            "facility_id": 803,
            "x": 1.0,
            "y": 1.0,
            "snap_limit": 45.0,
            "facility_only_footways": True,
            "modes": [],
        }
    ]
    station_graph, _missed = attach_stops_to_walk_graph(
        [[0.0, 20.0], [30.0, 20.0]],
        [[0, 1, 30.0, 1]],
        point_stops + entry,
        preferred_exterior_edges={0},
        street_edges={0},
    )
    assert entry[0]["walk_node"] >= 0
    linked = add_station_facility_fallback(
        station_graph, point_stops, facilities, entry
    )
    assert linked["estimated_station_facility_links"] == 2
    assert all(
        s["station_access_kind"] == "station_facility_estimated" for s in point_stops
    )
    assert all(s["walk_node"] >= 0 for s in point_stops)
    # Reject plausible nearby road-only anchors and a different named station.
    wrong_stops = [
        {
            "id": "n804",
            "name": "Other Station",
            "x": 0.0,
            "y": 0.0,
            "modes": ["train"],
            "walk_node": -1,
        }
    ]
    assert not match_station_facility_anchors(facilities, wrong_stops).get(
        "matched_facility_stops"
    )
    failed = [{**entry[0], "walk_node": -1}]
    road_only, _missed = attach_stops_to_walk_graph(
        [[0.0, 20.0], [30.0, 20.0]],
        [[0, 1, 30.0, 1]],
        wrong_stops + failed,
        street_edges={0},
        preferred_exterior_edges=set(),
    )
    assert failed[0]["walk_node"] == -1
    assert not add_station_facility_fallback(
        road_only, wrong_stops, facilities, failed
    ).get("estimated_station_facility_links", 0)
    # OTP-inspired generalized platform regression: points and open ways.
    pt_stop = {
        "id": "n99001",
        "name": "Test",
        "x": 98.0,
        "y": 5.0,
        "modes": ["train"],
        "walk_node": -1,
    }
    pt = StationEntranceCollector([pt_stop], (0.0, 0.0))
    pt._way_platform_names = {"w42": "Test"}
    matched = match_linear_and_point_platforms(
        pt, [pt_stop], {42: [0, 1]}, [[0, 0], [100, 0]]
    )
    assert matched["matched_point_or_linear_platform"] == 1
    assert "w42" in pt.platform_access["n99001"]
    # Parallel rail platforms with indistinguishable access cannot be guessed.
    pt.platform_access["n99001"].clear()
    pt.platform_objects.update(("n5", "n6"))
    pt.station_centers.update({"n5": (98, 10), "n6": (98, 0)})
    pt._way_platform_names.clear()
    matched = match_linear_and_point_platforms(pt, [pt_stop], {}, [])
    assert matched.get("ambiguous_platform_candidate") == 1
    assert not pt.platform_access["n99001"]
    # Platform-to-sidewalk connector must never cross a mapped surface rail.
    barrier = [((5.0, -5.0), (5.0, 5.0))]
    barrier_grid = railway_barrier_grid(barrier)
    assert inferred_link_crosses_rail((0, 0), (10, 0), barrier, barrier_grid)
    assert not inferred_link_crosses_rail((0, 0), (3, 0), barrier, barrier_grid)
    blocked_point = {
        "id": "n1000",
        "entrance_id": 1000,
        "platform_key": "n1000",
        "x": 0.0,
        "y": 0.0,
        "snap_limit": 15.0,
        "facility_only_footways": True,
        "modes": [],
    }
    no_cross, _missing = attach_stops_to_walk_graph(
        [[0.0, 0.0], [10.0, 0.0], [15.0, 0.0]],
        [[1, 2, 5.0, 1]],
        [blocked_point],
        osm_node_index={1000: 0},
        preferred_exterior_edges={0},
        street_edges={0},
        exterior_parents=array("I", [0, 1, 1]),
        exterior_roots={1},
        trusted_exterior_roots={1},
        railway_segments=barrier,
    )
    assert blocked_point["walk_node"] == -1
    assert not any(edge[3] == 5 for edge in no_cross["edges"])
    # A final quality report describes weak connectivity, not a directed proof.
    final_quality = rail_access_quality_report(
        {"nodes": [[0, 0], [1, 0], [10, 0]], "edges": [[0, 1, 1, 1]]},
        [
            {"id": "n1", "name": "Test", "modes": ["train"], "walk_node": 0},
            {"id": "n2", "name": "Test2", "modes": ["train"], "walk_node": 2},
        ],
        {1},
        {1},
    )
    assert final_quality["counts"]["connected_to_major_street"] == 1
    assert final_quality["counts"]["isolated_from_mapped_street"] == 1
    # The estimated interchange arc is not part of the ground graph.
    assert all(len(edge) == 4 for edge in graph["edges"])
    print("[test] PASS: outdoor short access, restricted bridges, ", end="")
    print("platform points/lines, barrier guards, quality, interchange, CSR")


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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the Moscow transport reachability map."
    )
    parser.add_argument(
        "-o",
        "--output",
        default="dist/routes.html",
        help="Output HTML path (default: dist/routes.html)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run standalone station/transfer regression tests",
    )
    parser.add_argument(
        "--cache-dir",
        default="build",
        help="Directory for downloads and intermediate files (default: build)",
    )
    parser.add_argument(
        "--through-service-policy",
        choices=("estimated", "strict"),
        default="estimated",
        help="Estimated: infer same-line reciprocal terminal turnarounds. "
        "Strict: never infer in-seat continuation between OSM relations.",
    )
    parser.add_argument(
        "--inspect-route-ref",
        metavar="REF",
        help="Inspect relation IDs and mode/quarantine for this ref; skip graph",
    )
    parser.add_argument(
        "--diagnose-station",
        metavar="NAME",
        help="Inspect raw OSM ways and graph connectivity around a rail station",
    )
    parser.add_argument(
        "--diagnose-only",
        action="store_true",
        help="Write access diagnostics without rebuilding the HTML or transfers",
    )
    parser.add_argument("--diagnose-radius", type=float, default=700.0)
    parser.add_argument(
        "--audit-rail-access",
        metavar="OUTPUT.json",
        type=Path,
        help="Audit street connectivity of all rail stops; use cached graph",
    )
    parser.add_argument(
        "--audit-rail-radius",
        type=float,
        default=650.0,
        help="Radius for nearby street stops in rail access audit (meters)",
    )
    parser.add_argument("--diagnose-output", default="station-access.json")
    parser.add_argument("--diagnose-geojson", default="station-access.geojson")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--rebuild-walking", action="store_true")
    parser.add_argument(
        "--allow-moskvorechye-private",
        action="store_true",
        help="TEST ONLY: allow 4 explicitly listed access=private station footways",
    )
    parser.add_argument(
        "--check-moskvorechye-only",
        action="store_true",
        help="Check cached/rebuilt pedestrian connectivity; skip HTML/transfers",
    )
    parser.add_argument(
        "--rebuild-transfers",
        action="store_true",
        help="Recompute the offline pedestrian stop-to-stop transfer graph",
    )
    parser.add_argument(
        "--walking-json",
        type=Path,
        help="Use a prebuilt walking graph for normalized GeoJSON fixtures",
    )
    parser.add_argument(
        "--refresh-web-assets",
        action="store_true",
        help="Refresh HTML/config and copy web assets without processing OSM",
    )
    parser.add_argument("--walk-minutes", type=float, default=5.0)
    parser.add_argument("--walk-speed", type=float, default=80.0)
    parser.add_argument("--grid-size", type=float, default=400.0)
    parser.add_argument("--circle-opacity", type=float, default=50.0)
    parser.add_argument(
        "--mkad-buffer-km",
        type=float,
        default=10.0,
        help="Include stops and route geometry within MKAD plus this many km",
    )
    parser.add_argument(
        "--mkad-geojson",
        type=Path,
        help="Use a locally saved MKAD polygon instead of the cached download",
    )
    parser.add_argument(
        "--no-mkad-clip",
        action="store_true",
        help="Disable the default MKAD + 10 km transport area limit",
    )
    parser.add_argument(
        "--input-geojson",
        type=Path,
        help="Use a normalized route/stop fixture instead of reading OSM PBF",
    )
    parser.add_argument(
        "--dump-geojson",
        type=Path,
        help="Save normalized route/stop features for debugging and tests",
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        help="Save model diagnostics (mode counts, provenance and gaps)",
    )
    parser.add_argument(
        "--infer-rail-stops",
        action="store_true",
        help="Opt into heuristic railway station recovery (unverified)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {PROJECT_VERSION}"
    )
    args = parser.parse_args()
    for name in ("walk_minutes", "walk_speed", "grid_size"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be greater than zero")
    if not 0 <= args.mkad_buffer_km <= 50:
        parser.error("--mkad-buffer-km must be between 0 and 50")
    if not 0 <= args.circle_opacity <= 100:
        parser.error("--circle-opacity must be between 0 and 100")
    if not 100 <= args.audit_rail_radius <= 2500:
        parser.error("--audit-rail-radius must be between 100 and 2500")
    if args.audit_rail_access and args.diagnose_station:
        parser.error("--audit-rail-access conflicts with --diagnose-station")
    if args.diagnose_radius < 100 or args.diagnose_radius > 2500:
        parser.error("--diagnose-radius must be between 100 and 2500")
    if args.diagnose_only and not args.diagnose_station:
        parser.error("--diagnose-only requires --diagnose-station")
    if args.check_moskvorechye_only and args.diagnose_station:
        parser.error("--check-moskvorechye-only conflicts with --diagnose-station")
    if args.check_moskvorechye_only and args.walking_json:
        parser.error("--check-moskvorechye-only requires original OSM input")
    if args.allow_moskvorechye_private and args.walking_json:
        parser.error("--allow-moskvorechye-private requires original OSM input")
    if args.update and args.rebuild:
        parser.error("--update and --rebuild cannot be used together")
    if args.input_geojson and not args.walking_json and not args.inspect_route_ref:
        parser.error("--input-geojson requires --walking-json for pedestrian routing")
    if args.input_geojson and (args.update or args.rebuild):
        parser.error("--input-geojson conflicts with --update and --rebuild")
    if args.input_geojson and args.diagnose_station:
        parser.error("--diagnose-station requires original OSM input")
    return args


def main():
    args = parse_args()
    build_started = time.monotonic()
    build_phases = []

    def step_done(name, started):
        duration = time.monotonic() - started
        build_phases.append((name, duration))
        print(f"[build] DONE {name}: {duration:.3f}s", flush=True)

    print(f"[build] START v{PROJECT_VERSION}", flush=True)
    if args.self_test:
        run_self_tests()
        return
    if args.refresh_web_assets:
        output = Path(args.output)
        data_file = output.with_name(output.stem + ".json.gz")
        config_file = output.with_name(output.stem + "-config.json")
        if not data_file.is_file():
            raise FileNotFoundError(f"Transport data missing: {data_file}")
        if config_file.exists():
            # Fast path: refreshing source assets does not read or decode gzip.
            old_config = json.loads(config_file.read_text(encoding="ascii"))
            packed = {
                "origin": [old_config["origin_lon"], old_config["origin_lat"]],
                "grid_size": old_config["grid_size"],
                "through_service_policy": (
                    "estimated" if old_config["infer_through_service"] else "strict"
                ),
            }
            walk_speed = old_config["walk_speed"]
            walk_minutes = old_config["initial_walk_minutes"]
            opacity = old_config["initial_opacity"]
        else:
            # Backwards-compatible refresh of sites generated before v0.15.10.
            old_html = output.read_text(encoding="ascii")
            old_js = output.with_suffix(".js").read_text(encoding="utf-8")
            packed = json.loads(gzip.decompress(data_file.read_bytes()))
            if packed.get("walking", {}).get("format") != "delta-varint-dm-v1":
                raise ValueError("Existing .json.gz is not a compatible walking graph")

            def old_constant(pattern, text):
                match = re.search(pattern, text, flags=re.DOTALL)
                if match is None:
                    raise ValueError(
                        "Cannot preserve old generated setting: " + pattern
                    )
                return float(match.group(1))

            packed["grid_size"] = old_constant(r"const GRID_SIZE = ([0-9.]+);", old_js)
            walk_speed = old_constant(
                r"const WALK_SPEED_M_PER_MIN = ([0-9.]+);", old_js
            )
            walk_minutes = old_constant(
                r'id="walk-time"[^>]*?value="([0-9.]+)"', old_html
            )
            opacity = old_constant(
                r'id="circle-opacity"[^>]*?value="([0-9.]+)"', old_html
            )
        print("[build] Refreshing page assets; keeping existing OSM data", flush=True)
        html = build_html(
            packed,
            walk_minutes,
            walk_speed,
            opacity,
            output,
            reuse_data=True,
        )
        write_atomic_bytes(output, html.encode("ascii"))
        print("[build] Web assets refreshed, transport data unchanged", flush=True)
        return
    if args.allow_moskvorechye_private:
        print(
            "[station] EXPERIMENTAL: 4 access=private ways allowed for testing",
            flush=True,
        )
        print("[station] This does NOT establish public access rights.", flush=True)
    phase_started = time.monotonic()
    print("[build] START transport input", flush=True)
    if args.input_geojson:
        feature_collection = json.loads(args.input_geojson.read_text(encoding="utf-8"))
    else:
        if osmium is None:
            raise RuntimeError(
                "Install osmium for OSM PBF extraction: pip install osmium"
            )

        transport_pbf, raw_source = build_cache(
            Path(args.cache_dir), update=args.update, rebuild=args.rebuild
        )
        feature_collection = build_transport_data(
            transport_pbf, infer_rail_stops=args.infer_rail_stops
        )
    step_done("transport input", phase_started)
    if args.inspect_route_ref:
        inspected = inspect_route_ref(feature_collection, args.inspect_route_ref)
        if args.report_json:
            args.report_json.write_text(
                json.dumps(inspected, ensure_ascii=True, indent=2) + "\n",
                encoding="ascii",
            )
        return
    if not args.no_mkad_clip:
        phase_started = time.monotonic()
        print("[build] START MKAD area clipping", flush=True)
        region = load_mkad_region(
            args.cache_dir, args.mkad_geojson, args.mkad_buffer_km
        )
        feature_collection = clip_transport_features(feature_collection, region)
        del region
        if not feature_collection["features"]:
            raise RuntimeError("MKAD boundary filter removed all transport features")
        step_done("MKAD area clipping", phase_started)
    else:
        print("[area] MKAD clipping disabled", flush=True)
    phase_started = time.monotonic()
    print("[build] START optional GeoJSON export", flush=True)
    if args.dump_geojson:
        args.dump_geojson.write_text(
            json.dumps(feature_collection, ensure_ascii=True) + "\n",
            encoding="ascii",
        )
    step_done("optional GeoJSON export", phase_started)
    phase_started = time.monotonic()
    print("[build] START transport index", flush=True)
    index = build_index(feature_collection, args.grid_size)
    index["through_service_policy"] = args.through_service_policy
    print(f"[routes] terminal continuity policy: {args.through_service_policy}")
    print(f"indexed stops: {len(index['stops'])}")
    print(f"indexed routes: {len(index['routes'])}")
    print(f"grid cells: {len(index['grid'])}")
    step_done("transport index", phase_started)
    phase_started = time.monotonic()
    print("[build] START pedestrian graph or cache", flush=True)
    if args.walking_json:
        walking = json.loads(args.walking_json.read_text(encoding="ascii"))
        stop_nodes = walking.pop("stop_nodes", None)
        if stop_nodes is None or len(stop_nodes) != len(index["stops"]):
            raise ValueError("Walking fixture must include matching stop_nodes")
        for stop, node in zip(index["stops"], stop_nodes):
            stop["walk_node"] = node
    else:
        cache_dir = Path(args.cache_dir)
        walking = load_or_build_pedestrian(
            cache_dir,
            raw_source,
            index,
            MAX_WALK_UI_MINUTES * args.walk_speed,
            rebuild=(args.update or args.rebuild_walking),
            allow_private_station_ways=args.allow_moskvorechye_private,
        )
    if not walking.get("nodes") or not walking.get("edges"):
        raise RuntimeError("Pedestrian graph is empty")
    step_done("pedestrian graph or cache", phase_started)
    phase_started = time.monotonic()
    print("[build] START graph validation", flush=True)
    node_count = len(walking["nodes"])
    for edge in walking["edges"]:
        if edge[0] >= node_count or edge[1] >= node_count:
            raise ValueError("Pedestrian graph contains an invalid edge")
    index["walking"] = walking
    step_done("graph validation", phase_started)
    if args.allow_moskvorechye_private or args.check_moskvorechye_only:
        station_ok = check_moskvorechye_connection(walking, index["stops"])
        if not station_ok:
            print(
                "[station] The four private ways alone have NOT fixed access.",
                flush=True,
            )
            print(
                "[station] Inspect station-access.geojson before adding links.",
                flush=True,
            )
        if args.check_moskvorechye_only:
            raise SystemExit(0 if station_ok else 2)
    if args.audit_rail_access:
        phase_started = time.monotonic()
        print("[build] START all rail-station access audit", flush=True)
        access_report = audit_all_rail_access(
            walking, index["stops"], args.audit_rail_radius
        )
        args.audit_rail_access.write_text(
            json.dumps(access_report, ensure_ascii=True, indent=2) + "\n",
            encoding="ascii",
        )
        print("[rail-access] saved:", args.audit_rail_access, flush=True)
        step_done("all rail-station access audit", phase_started)
        return
    if args.diagnose_station:
        phase_started = time.monotonic()
        print("[build] START station access diagnosis", flush=True)
        write_station_access_diagnostic(args, index, walking, raw_source)
        step_done("station access diagnosis", phase_started)
        if args.diagnose_only:
            print("[build] DONE diagnostic-only run", flush=True)
            return
    phase_started = time.monotonic()
    print("[build] START walking transfers or cache", flush=True)
    walking_file = (
        PEDESTRIAN_TEST_CACHE_NAME
        if args.allow_moskvorechye_private
        else PEDESTRIAN_CACHE_NAME
    )
    walk_source = args.walking_json or (Path(args.cache_dir) / walking_file)
    index["walk_transfers"] = load_or_build_walk_transfers(
        Path(args.cache_dir),
        walk_source,
        walking,
        index["stops"],
        MAX_WALK_UI_MINUTES * args.walk_speed,
        rebuild=(
            args.rebuild_transfers
            or args.rebuild_walking
            or args.update
            or args.rebuild
        ),
        allow_private_station_ways=args.allow_moskvorechye_private,
    )
    step_done("walking transfers or cache", phase_started)
    if args.allow_moskvorechye_private:
        check_moskvorechye_transfers(index["walk_transfers"], index["stops"])
    phase_started = time.monotonic()
    print("[build] START statistics and audit", flush=True)
    for stop in index["stops"]:
        if stop.get("walk_node", -1) >= node_count:
            raise ValueError("Pedestrian graph contains an invalid stop node")
    walk_connected = sum(s.get("walk_node", -1) >= 0 for s in index["stops"])
    print(f"walking stops connected: {walk_connected}/{len(index['stops'])}")
    report = audit_index(index)
    report["butovo_line"] = audit_butovo_line(index)
    report["paveletsky_corridor"] = audit_paveletsky_corridor(index, feature_collection)
    report["metro_interchanges"] = audit_named_interchanges(index)
    hints = walking.get("interchange_hints", [])
    report["estimated_interchange_hints"] = {
        "count": len(hints),
        "examples": [
            {
                "from": index["stops"][a]["name"],
                "to": index["stops"][b]["name"],
                "meters": meters,
                "stop_area_group": group,
            }
            for a, b, meters, group in hints[:40]
        ],
    }
    report["extraction_audit"] = feature_collection.get("extraction_audit", {})
    report["rail_station_audit"] = audit_rail_stations(index, feature_collection)
    report["rail_boarding_access"] = rail_boarding_access_report(index["stops"])
    report["platform_link_audit"] = walking.get("platform_link_audit", {})
    report["rail_access_quality"] = walking.get("rail_access_quality", {})
    geometry_edges = [
        edge
        for route in index["routes"].values()
        for direction in route["directions"]
        for edge in direction.get("edge_meters", [])
    ]
    trusted_count = sum(edge is not None for edge in geometry_edges)
    report["travel_model"] = {
        "source": "OSM topology with heuristic speeds, no schedules",
        "geometry_based_edges": trusted_count,
        "estimated_distance_edges": len(geometry_edges) - trusted_count,
        "no_assumed_through_service_between_directions": True,
        "boarding_waits_are_fixed_estimates": True,
    }
    if args.report_json:
        report["rail_walking_components"] = audit_station_walk_components(
            walking,
            index["stops"],
        )
    for station in report["rail_station_audit"]:
        print(
            "[audit]",
            station["query"],
            "matches:",
            len(station["stops"]),
            "OSM station objects:",
            len(station["osm_station_candidates"]),
        )
    for stop in index["stops"]:
        # UI uses route keys; keep audit-only metadata out of the HTML.
        stop.pop("modes", None)
        stop.pop("station_access_kind", None)
    report["pedestrian"] = {
        "transfer_arcs": len(index["walk_transfers"]["to"]),
        "nodes": len(walking["nodes"]),
        "edges": len(walking["edges"]),
        "connected_stops": walk_connected,
        "disconnected_stops": len(index["stops"]) - walk_connected,
        "sample_unconnected_stops": [
            {"id": s["id"], "name": s.get("name")}
            for s in index["stops"]
            if s.get("walk_node", -1) < 0
        ][:30],
    }
    print(
        f"pedestrian graph: {len(walking['nodes'])} nodes, "
        f"{len(walking['edges'])} edges"
    )
    print(f"mode directions: {report['mode_directions']}")
    print(f"geometry sources: {report['geometry_sources']}")
    if report["synthetic_route_relations"]:
        print("WARNING: unverified route relation IDs found in input")
    if not report["mode_directions"].get("mcc"):
        print("WARNING: no MCC route relations; no artificial ring is created")
    if not report["mode_directions"].get("train"):
        print("WARNING: no non-MCD suburban rail routes found in this OSM extract")
    if report["suspicious_stop_gaps_over_15km"]:
        print("WARNING: unusually long stop-to-stop gaps; see --report-json")
    if args.report_json:
        args.report_json.write_text(
            json.dumps(report, ensure_ascii=True, indent=4) + "\n", encoding="ascii"
        )
    step_done("statistics and audit", phase_started)
    if not args.input_geojson:
        from major_roads import generate as generate_major_roads

        roads_path = Path(args.output).with_name("moscow-major-roads.json")
        outdated = (
            not roads_path.is_file()
            or roads_path.stat().st_mtime_ns < Path(raw_source).stat().st_mtime_ns
            or roads_path.stat().st_mtime_ns
            < (Path(__file__).parent / "major_roads.py").stat().st_mtime_ns
            or args.update
            or args.rebuild
        )
        if outdated:
            phase_started = time.monotonic()
            print("[build] START major road illustration", flush=True)
            generate_major_roads(raw_source, roads_path)
            step_done("major road illustration", phase_started)
    phase_started = time.monotonic()
    print("[build] START HTML encode + gzip", flush=True)
    html = build_html(
        index,
        args.walk_minutes,
        args.walk_speed,
        args.circle_opacity,
        Path(args.output),
    )
    step_done("HTML encode + gzip", phase_started)
    phase_started = time.monotonic()
    print("[build] START HTML file write", flush=True)
    write_atomic_bytes(Path(args.output), html.encode("ascii"))
    step_done("HTML file write", phase_started)
    print(f"wrote: {Path(args.output).resolve()}")
    print("[build] FINAL TIMINGS (seconds)", flush=True)
    for name, duration in build_phases:
        print(f"[build] {name}: {duration:.3f}s", flush=True)
    print(f"[build] TOTAL: {time.monotonic()-build_started:.3f}s", flush=True)


if __name__ == "__main__":
    main()
