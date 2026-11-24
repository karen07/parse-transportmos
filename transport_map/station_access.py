"""Station access for the Moscow transport map."""

import math
from collections import Counter, defaultdict

from .station_osm import StationEntranceCollector, canonical_station_name
from .walking import (
    MOSKVORECHYE_PLATFORM_IDS,
    build_walk_segment_grid,
    inferred_link_crosses_rail,
    nearest_walk_segment,
    node_in_exterior_component,
    railway_barrier_grid,
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
