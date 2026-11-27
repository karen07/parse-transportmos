"""Walking for the Moscow transport map."""

import math
from array import array
from collections import Counter, defaultdict

from .common import EARTH_RADIUS_M, WALK_DENIED, OsmiumSimpleHandler, project
from .station_osm import StationEntranceCollector, canonical_station_name

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


WALK_GRID_M = 100.0


# Explicit test-only override of four OSM ways found by the station inspector.
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
