"""Area for the Moscow transport map."""

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from .common import (
    MKAD_GEOJSON_CACHE_NAME,
    MKAD_GEOJSON_URL,
    SURFACE_MODES,
    coord_distance_m,
    project,
)
from .osm_sources import download_source


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
