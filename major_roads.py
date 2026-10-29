#!/usr/bin/env python3
"""Generate a compact, topologically joined road drawing from Moscow OSM PBF.

This is a *visual* asset for the loading screen, not a routing graph.
Only major ways and selected ring-road names are rendered. No guessed bridges.
"""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

try:
    import osmium
except ImportError:
    osmium = None

SimpleHandler = osmium.SimpleHandler if osmium is not None else object

# Equirectangular metric projection around central Moscow. Fixed extents
# ensure that MKAD stays in frame and the result is stable between builds.
CENTER_LON = 37.62
CENTER_LAT = 55.752
METERS_PER_DEGREE_LAT = 111195.0
METERS_PER_DEGREE_LON = METERS_PER_DEGREE_LAT * math.cos(math.radians(CENTER_LAT))
VIEW_X_METERS = 34000.0
VIEW_Y_METERS = 25500.0
COORD_UNIT_METERS = 20.0
MAX_X = VIEW_X_METERS / COORD_UNIT_METERS
MAX_Y = VIEW_Y_METERS / COORD_UNIT_METERS
CLASS_NAMES = ("mkad", "ring", "motorway", "trunk", "primary")
CLASS_INDEX = {key: index for index, key in enumerate(CLASS_NAMES)}
MAIN_HIGHWAYS = {"motorway", "trunk", "primary"}
RING_NAMES = (
    "\u043c\u043a\u0430\u0434",
    "\u0442\u0442\u043a",
    "\u0442\u0440\u0435\u0442\u044c\u0435",
    "\u0441\u0430\u0434\u043e\u0432",
    "\u0431\u0443\u043b\u044c\u0432\u0430\u0440\u043d",
)


def road_class(tags):
    """Only trunk lines, with named ring segments kept across class changes."""
    highway = tags.get("highway") or ""
    if not highway or highway.endswith("_link") or tags.get("area") == "yes":
        return None
    if highway not in MAIN_HIGHWAYS | {"secondary", "tertiary"}:
        return None
    name = " ".join(tags.get(k) or "" for k in ("name", "name:ru", "name:en", "ref"))
    name = name.casefold()
    if "\u043c\u043a\u0430\u0434" in name or "moscow ring road" in name:
        return "mkad"
    if any(keyword in name for keyword in RING_NAMES[1:]):
        return "ring"
    return highway if highway in MAIN_HIGHWAYS else None


def project(lon, lat):
    return (
        (lon - CENTER_LON) * METERS_PER_DEGREE_LON / COORD_UNIT_METERS,
        (CENTER_LAT - lat) * METERS_PER_DEGREE_LAT / COORD_UNIT_METERS,
    )


def line_clip(a, b):
    """Liang-Barsky clipping: no artificial connections across outside areas."""
    x0, y0 = a
    x1, y1 = b
    dx, dy = x1 - x0, y1 - y0
    p = (-dx, dx, -dy, dy)
    q = (x0 + MAX_X, MAX_X - x0, y0 + MAX_Y, MAX_Y - y0)
    lower, upper = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-12:
            if qi < 0:
                return None
            continue
        t = qi / pi
        if pi < 0:
            lower = max(lower, t)
        else:
            upper = min(upper, t)
        if lower > upper:
            return None
    return (x0 + lower * dx, y0 + lower * dy), (x0 + upper * dx, y0 + upper * dy)


def clipped_paths(coords):
    """Clip independently; disconnected pieces must never be joined by a jump."""
    current = []
    for a, b in zip(coords, coords[1:]):
        clipped = line_clip(a, b)
        if clipped is None:
            if len(current) > 1:
                yield current
            current = []
            continue
        start, end = clipped
        if current and math.dist(current[-1], start) > 1e-5:
            if len(current) > 1:
                yield current
            current = []
        if not current:
            current.append(start)
        current.append(end)
    if len(current) > 1:
        yield current


def point_segment_distance(point, a, b):
    ax, ay = a
    bx, by = b
    px, py = point
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / max(dx * dx + dy * dy, 1e-20)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


def simplify(coords, epsilon=0.65):
    """RDP with preserved endpoints, in 20m units (13m tolerance)."""
    if len(coords) < 3:
        return coords
    keep = {0, len(coords) - 1}
    pending = [(0, len(coords) - 1)]
    while pending:
        start, end = pending.pop()
        greatest = 0.0
        index = -1
        for i in range(start + 1, end):
            distance = point_segment_distance(coords[i], coords[start], coords[end])
            if distance > greatest:
                greatest, index = distance, i
        if greatest > epsilon:
            keep.add(index)
            pending.extend(((start, index), (index, end)))
    return [coords[i] for i in sorted(keep)]


def merge_chains(ways, positions=None):
    """Join ways only at the same OSM node, never by screen proximity.

    A junction with degree != 2 stops joining, but all branches still share
    precisely the same node coordinate when rendered with round line caps.
    """
    endpoint_index = defaultdict(list)
    for index, (kind, refs) in enumerate(ways):
        endpoint_index[(kind, refs[0])].append((index, True))
        endpoint_index[(kind, refs[-1])].append((index, False))
    used = set()
    for index, (kind, refs) in enumerate(ways):
        if index in used:
            continue
        used.add(index)
        chain = list(refs)
        for from_start in (True, False):
            while True:
                endpoint = chain[0] if from_start else chain[-1]
                touching = endpoint_index[(kind, endpoint)]
                candidates = [item for item in touching if item[0] not in used]
                if not candidates:
                    break
                if len(touching) == 2 and len(candidates) == 1:
                    neighbor, neighbor_start = candidates[0]
                elif positions is not None:
                    near = chain[1] if from_start else chain[-2]
                    origin, before = positions.get(endpoint), positions.get(near)
                    if origin is None or before is None:
                        break
                    dx, dy = origin[0] - before[0], origin[1] - before[1]
                    norm = math.hypot(dx, dy)
                    if norm < 1e-8:
                        break
                    ranked = []
                    for neighbor, neighbor_start in candidates:
                        candidate_refs = ways[neighbor][1]
                        next_node = (
                            candidate_refs[1] if neighbor_start else candidate_refs[-2]
                        )
                        after = positions.get(next_node)
                        if after is None:
                            continue
                        ex, ey = after[0] - origin[0], after[1] - origin[1]
                        length = math.hypot(ex, ey)
                        if length > 1e-8:
                            cosine = (dx * ex + dy * ey) / (norm * length)
                            ranked.append((cosine, neighbor, neighbor_start))
                    ranked.sort(reverse=True)
                    if not ranked or ranked[0][0] < 0.73:
                        break
                    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.13:
                        break
                    _, neighbor, neighbor_start = ranked[0]
                else:
                    break
                additional = ways[neighbor][1]
                if from_start:
                    extension = (
                        list(reversed(additional))
                        if neighbor_start
                        else list(additional)
                    )
                    chain = extension[:-1] + chain
                else:
                    extension = (
                        list(additional)
                        if neighbor_start
                        else list(reversed(additional))
                    )
                    chain.extend(extension[1:])
                used.add(neighbor)
        yield kind, chain


class WayCollector(SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = []
        self.refs = set()

    def way(self, way):
        kind = road_class(way.tags)
        if kind is None:
            return
        refs = [node.ref for node in way.nodes]
        if len(refs) < 2:
            return
        self.ways.append((kind, refs))
        self.refs.update(refs)


class NodeCollector(SimpleHandler):
    def __init__(self, needed):
        super().__init__()
        self.needed = needed
        self.positions = {}

    def node(self, node):
        if node.id in self.needed and node.location.valid():
            self.positions[node.id] = project(node.location.lon, node.location.lat)


def generate_from_ways(ways, positions):
    """Expose a pure-function core for unit testing without pyosmium."""
    output = []
    counts = defaultdict(int)
    for kind, refs in merge_chains(ways, positions):
        # Never bridge missing PBF nodes; break the polyline instead.
        contiguous = []
        for ref in list(refs) + [None]:
            if ref not in positions:
                if len(contiguous) >= 2:
                    for line in clipped_paths(contiguous):
                        points = simplify(line)
                        rounded = []
                        for x, y in points:
                            pair = [round(x), round(y)]
                            if not rounded or pair != rounded[-1]:
                                rounded.append(pair)
                        if len(rounded) < 2:
                            continue
                        coords = [value for pair in rounded for value in pair]
                        if len(coords) >= 4:
                            output.append([CLASS_INDEX[kind], coords])
                            counts[kind] += 1
                contiguous = []
            else:
                contiguous.append(positions[ref])
    return {
        "format": "moscow-major-roads-v1",
        "unit_m": COORD_UNIT_METERS,
        "extent": [MAX_X, MAX_Y],
        "classes": list(CLASS_NAMES),
        "paths": output,
        "counts": dict(counts),
        "attribution": "OpenStreetMap contributors",
    }


def generate(input_path, output_path):
    if osmium is None:
        raise RuntimeError("Install pyosmium: python3 -m pip install osmium")
    print("[roads] scanning major ways:", input_path, flush=True)
    collector = WayCollector()
    collector.apply_file(str(input_path))
    print("[roads] way count:", len(collector.ways), flush=True)
    nodes = NodeCollector(collector.refs)
    nodes.apply_file(str(input_path))
    print("[roads] selected nodes:", len(nodes.positions), flush=True)
    data = generate_from_ways(collector.ways, nodes.positions)
    if not data["paths"]:
        raise RuntimeError("No major roads found inside the Moscow view")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pending = output_path.with_suffix(output_path.suffix + ".tmp")
    pending.write_text(
        json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n"
    )
    pending.replace(output_path)
    print("[roads] wrote:", output_path, "paths:", len(data["paths"]), flush=True)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.input, args.output)
