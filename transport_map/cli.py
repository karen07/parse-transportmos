"""Cli for the Moscow transport map."""

import argparse
import gzip
import json
import re
import time
from pathlib import Path

from .area import clip_transport_features, load_mkad_region
from .common import (
    MERGED_SOURCE_NAME,
    PEDESTRIAN_CACHE_NAME,
    PEDESTRIAN_TEST_CACHE_NAME,
    PROJECT_VERSION,
    osmium,
)
from .diagnostics import (
    audit_all_rail_access,
    audit_butovo_line,
    audit_index,
    audit_named_interchanges,
    audit_paveletsky_corridor,
    audit_rail_stations,
    audit_station_walk_components,
    check_moskvorechye_connection,
    rail_boarding_access_report,
    write_station_access_diagnostic,
)
from .osm_sources import build_cache, prepare_raw_source
from .pedestrian import load_or_build_pedestrian
from .self_tests import run_self_tests
from .site_builder import build_html, publish_boot_svg, write_atomic_bytes
from .transfers import check_moskvorechye_transfers, load_or_build_walk_transfers
from .transport import build_transport_data, inspect_route_ref
from .transport_index import build_index
from .walking import MAX_WALK_UI_MINUTES


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the Moscow transport reachability map."
    )
    parser.add_argument(
        "-o",
        "--output",
        default="site/routes.html",
        help="Output HTML path (default: site/routes.html)",
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
    parser.add_argument(
        "--boot-roads-only",
        "--boot-svg-only",
        action="store_true",
        help="Generate the standalone road SVG without rebuilding transport data",
    )
    parser.add_argument(
        "--boot-zoom",
        type=int,
        choices=(10, 11, 12),
        default=11,
        help="Loading screen road detail profile (default: 11)",
    )
    parser.add_argument(
        "--boot-pbf",
        type=Path,
        help="Local full OSM PBF for --boot-roads-only; otherwise use regional cache",
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
    if args.boot_pbf and not args.boot_roads_only:
        raise ValueError("--boot-pbf requires --boot-roads-only")
    if args.boot_roads_only:
        from .major_roads import generate as generate_major_roads

        output = Path(args.output)
        if osmium is None:
            raise RuntimeError("Install osmium: python3 -m pip install osmium")
        if args.boot_pbf:
            raw_source = args.boot_pbf
        else:
            cached = Path(args.cache_dir) / MERGED_SOURCE_NAME
            if cached.is_file() and not args.update:
                raw_source = cached
            else:
                raw_source, _ = prepare_raw_source(Path(args.cache_dir), args.update)
        generate_major_roads(
            raw_source, Path(args.cache_dir) / "moscow-major-roads.json", args.boot_zoom
        )
        svg_path = publish_boot_svg(Path(args.cache_dir), output)
        print(f"[boot] wrote: {svg_path}", flush=True)
        if not args.refresh_web_assets:
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
            cache_dir=Path(args.cache_dir),
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
        from .major_roads import ROAD_FORMAT
        from .major_roads import generate as generate_major_roads

        roads_path = Path(args.cache_dir) / "moscow-major-roads.json"
        outdated = (
            not roads_path.is_file()
            or roads_path.stat().st_mtime_ns < Path(raw_source).stat().st_mtime_ns
            or roads_path.stat().st_mtime_ns
            < (Path(__file__).parent / "major_roads.py").stat().st_mtime_ns
            or args.update
            or args.rebuild
        )
        if not outdated:
            try:
                cached_roads = json.loads(roads_path.read_text(encoding="ascii"))
                outdated = (
                    cached_roads.get("format") != ROAD_FORMAT
                    or cached_roads.get("zoom") != args.boot_zoom
                )
            except (OSError, ValueError):
                outdated = True
        if outdated:
            phase_started = time.monotonic()
            print("[build] START major road illustration", flush=True)
            generate_major_roads(raw_source, roads_path, args.boot_zoom)
            step_done("major road illustration", phase_started)
    phase_started = time.monotonic()
    print("[build] START HTML encode + gzip", flush=True)
    html = build_html(
        index,
        args.walk_minutes,
        args.walk_speed,
        args.circle_opacity,
        Path(args.output),
        cache_dir=Path(args.cache_dir),
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
