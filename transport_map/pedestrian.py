"""Pedestrian for the Moscow transport map."""

import gzip
import json
import time
from array import array
from collections import Counter, defaultdict
from pathlib import Path

from .common import (
    PEDESTRIAN_CACHE_NAME,
    PEDESTRIAN_TEST_CACHE_NAME,
    cache_implementation,
    osmium,
    stop_fingerprint,
)
from .station_access import (
    add_station_access_links,
    add_station_facility_fallback,
    attach_stops_to_walk_graph,
    match_station_facility_anchors,
    station_interchange_hints,
)
from .station_osm import StationEntranceCollector
from .walking import (
    MOSKVORECHYE_PRIVATE_WAYS,
    WalkNodeCollector,
    WalkWayCollector,
    choose_boarding_platforms,
    exterior_component_edges,
    inferred_platform_point_stops,
    link_platform_surfaces,
    match_linear_and_point_platforms,
    match_nearby_platform_polygons,
)


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


def load_or_build_pedestrian(
    cache_dir, source, index, limit_m, rebuild=False, allow_private_station_ways=False
):
    filename = (
        PEDESTRIAN_TEST_CACHE_NAME
        if allow_private_station_ways
        else PEDESTRIAN_CACHE_NAME
    )
    path = cache_dir / filename
    private_ways = (
        sorted(MOSKVORECHYE_PRIVATE_WAYS) if allow_private_station_ways else []
    )
    implementation = cache_implementation()
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
            cached.get("stop_fingerprint") == fingerprint
            and cached.get("allowed_private_ways") == private_ways
            and cached.get("implementation") == implementation
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
        "implementation": implementation,
        "stop_fingerprint": fingerprint,
        "walk_limit_m": limit_m,
        "source_key": source_key,
        "allowed_private_ways": private_ways,
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
