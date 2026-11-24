"""Diagnostics for the Moscow transport map."""

import json
import math
import time
from array import array
from collections import Counter, defaultdict
from pathlib import Path

from .common import EARTH_RADIUS_M, SURFACE_MODES, OsmiumSimpleHandler, osmium, project
from .transport import RAIL_AUDIT_TERMS, relevant_rail_audit_name
from .walking import (
    MOSKVORECHYE_PLATFORM_IDS,
    allow_station_private_way,
    is_linear_station_walkway,
    is_walkable_way,
)

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
