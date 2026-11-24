"""Self tests for the Moscow transport map."""

import re
from array import array

from .common import coord_distance_m
from .diagnostics import audit_paveletsky_corridor
from .pedestrian import rail_access_quality_report
from .site_builder import WEB_SOURCE_DIR
from .station_access import (
    add_station_access_links,
    add_station_facility_fallback,
    attach_stops_to_walk_graph,
    match_station_facility_anchors,
    station_interchange_hints,
)
from .station_osm import StationEntranceCollector
from .transfers import merge_interchange_hints
from .transport import (
    incompatible_route_way_evidence,
    inspect_route_ref,
    normalized_route_ref,
    resolve_route_mode_and_master,
    resolved_route_member_runs,
    route_identity,
    transport_mode,
)
from .transport_index import (
    conservative_geometry_edge_lengths,
    pair_surface_route_directions,
)
from .walking import (
    exterior_component_edges,
    inferred_link_crosses_rail,
    is_exterior_walkway,
    is_linear_station_walkway,
    is_walkable_way,
    link_platform_surfaces,
    match_linear_and_point_platforms,
    match_nearby_platform_polygons,
    node_in_exterior_component,
    railway_barrier_grid,
)


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
    assert "tripBackTop.addEventListener('click', backToReachability)" in routes_js
    assert "tripBack.addEventListener('click', backToReachability)" in routes_js
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
