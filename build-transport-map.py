#!/usr/bin/env python3
"""Build a single-file map from OSM data or a normalized GeoJSON fixture."""

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from transportmap import PROJECT_VERSION
from transportmap.audit import audit_index
from transportmap.index import build_index
from transportmap.renderer import build_html


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the Moscow transport reachability map."
    )
    parser.add_argument("-o", "--output", default="routes.html")
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--walk-minutes", type=float, default=5.0)
    parser.add_argument("--walk-speed", type=float, default=80.0)
    parser.add_argument("--grid-size", type=float, default=400.0)
    parser.add_argument("--circle-opacity", type=float, default=40.0)
    parser.add_argument(
        "--input-geojson", type=Path,
        help="Use a normalized route/stop fixture instead of reading OSM PBF",
    )
    parser.add_argument(
        "--dump-geojson", type=Path,
        help="Save normalized route/stop features for debugging and tests",
    )
    parser.add_argument(
        "--report-json", type=Path,
        help="Save model diagnostics (mode counts, provenance and gaps)",
    )
    parser.add_argument(
        "--infer-rail-stops", action="store_true",
        help="Opt into heuristic railway station recovery (unverified)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {PROJECT_VERSION}"
    )
    args = parser.parse_args()
    for name in ("walk_minutes", "walk_speed", "grid_size"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be greater than zero")
    if not 0 <= args.circle_opacity <= 100:
        parser.error("--circle-opacity must be between 0 and 100")
    if args.update and args.rebuild:
        parser.error("--update and --rebuild cannot be used together")
    if args.input_geojson and (args.update or args.rebuild):
        parser.error("--input-geojson conflicts with --update and --rebuild")
    return args


def main():
    args = parse_args()
    if args.input_geojson:
        feature_collection = json.loads(args.input_geojson.read_text(encoding="utf-8"))
    else:
        # Optional for fixture-based tests, mandatory for full OSM extraction.
        from transportmap.osm_data import build_cache, build_transport_data

        transport_pbf = build_cache(
            Path(args.cache_dir), update=args.update, rebuild=args.rebuild
        )
        feature_collection = build_transport_data(
            transport_pbf, infer_rail_stops=args.infer_rail_stops
        )
    if args.dump_geojson:
        args.dump_geojson.write_text(
            json.dumps(feature_collection, ensure_ascii=True) + "\n",
            encoding="ascii",
        )
    index = build_index(feature_collection, args.grid_size)
    print(f"indexed stops: {len(index['stops'])}")
    print(f"indexed routes: {len(index['routes'])}")
    print(f"grid cells: {len(index['grid'])}")
    report = audit_index(index)
    print(f"mode directions: {report['mode_directions']}")
    print(f"geometry sources: {report['geometry_sources']}")
    if report["synthetic_route_relations"]:
        print("WARNING: unverified route relation IDs found in input")
    if not report["mode_directions"].get("mcc"):
        print("WARNING: no MCC route relations; no artificial ring is created")
    if report["suspicious_stop_gaps_over_15km"]:
        print("WARNING: unusually long stop-to-stop gaps; see --report-json")
    if args.report_json:
        args.report_json.write_text(
            json.dumps(report, ensure_ascii=True, indent=4) + "\n", encoding="ascii"
        )
    html = build_html(
        index, args.walk_minutes, args.walk_speed, args.circle_opacity
    )
    Path(args.output).write_text(html, encoding="ascii")
    print(f"wrote: {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
