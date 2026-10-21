# Moscow Transport Map v0.12.2 - Conservative Route Model

Single-file interactive Moscow travel-time reachability map generated from
OSMlab RU-MOW and RU-MOS PBF files. The UI builds a standalone `routes.html`.
No continuously running backend, GTFS data, or `osmium-tool` binary is needed.
The Python `osmium` module is required to process real PBF files.

## Fix versus v0.12.1: HTML Tidy trailing whitespace

The custom `format-go` runs `tidy -modify` and checks trailing whitespace
**after** Tidy has modified `routes.html`. The Python generator already
strips trailing whitespace before writing the file; Tidy can reinsert it.
Fixing this reliably requires a post-Tidy cleanup in the Go formatter.

This release includes `format-go-tidy-whitespace.patch`, which adds that
cleanup immediately after the existing Tidy invocation. This keeps the
100-column line check and all other checks enabled. Apply it to the
**repository-root** `format.go` once and rebuild `format-go`:

```
cd /path/to/git
patch -p1 < parse-transportmos/format-go-tidy-whitespace.patch
go build -o format-go format.go
./parse-transportmos/build-transport-map.py
./format-go
```

Extract the archive into `parse-transportmos/` before applying the patch:

```
tar -xf transport-map-osmlab-v0.12.2.tar -C parse-transportmos
```

Make a backup or commit `format.go` before patching. The patch is based on
the formatter source previously supplied in this conversation. If that
source has since changed, check the diff before applying. Do not patch
`format-go` binary; rebuild it from the patched Go source.

The generator retains **0 transfers** by default, with up to 2 selectable.
No route calculation, stop linking or cache schema was changed in v0.12.2.

## Fixes versus v0.12.0

- Initial transfer slider value is **0**; optional maximum remains **2**.
- Generated `routes.html` has no blank or whitespace-only lines. This avoids
  Tidy creating indented empty lines that trigger trailing-whitespace checks.
- Long inline SVG path attributes are split into equivalent shorter paths so
  HTML Tidy's indentation has room below the 100-character line limit.
- Offline self-tests verify the transfer slider default and upper limit.

## Changes versus v0.11.2

1. Stops inventing in-vehicle through-service between distinct OSM route
   directions. An OSM `route_master` is a line grouping, **not** a trip with
   a guaranteed through-running vehicle. Boarding another direction must be
   treated as another boarding/transfer. This affects the RAPTOR-style scan
   and itinerary enumeration. The UI still allows up to **two transfers**.
1. A railway route number such as `14` no longer identifies the MCC without
   explicit MCC metadata. Parent rail relations cannot reclassify subway
   routes as commuter rail or revive an excluded intercity service.
1. Inspects OSM route-member way tags during extraction. Quarantines a
   strong type mismatch, such as a purported `route=train` with at least
   two motor-road `highway` ways and **no** passenger railway ways. Does not
   silently convert such a relation to a bus. The output diagnostics contain
   source OSM relation IDs and reasons. A single incidental way, or a rail
   relation with no ways at all, is not grounds for quarantine.
1. Computes mapped distance between consecutive transport stops from the
   OSM route geometry only when the stops snap closely, follow the geometry
   in order, constituent ways are connected, and the length is plausible.
   Every rejected segment falls back to the former stop-chord/circuity
   estimate. Vehicle speed, wait and stop dwell remain model assumptions.
1. Adds a `--inspect-route-ref` command for rapid OSM relation inspection
   without a walking-graph or HTML rebuild. Handles the equivalent Latin
   `k` and Cyrillic `к`, e.g. `356k` / `356к`.
1. `report.json` now includes `travel_model` and, under `extraction_audit`,
   `quarantined_route_way_conflicts` alongside the earlier master conflicts.

## Retained behavior

- One standalone `routes.html` with compressed data, RAPTOR-style routing,
  walking graph, progressive canvas rendering, and MKAD plus 10 km clipping.
- Combined **МЦД + Электрички** switch, initially OFF; surface transport
  initially ON, metro and MCC initially OFF.
- Default 0 transfers (0, 1, 2 selectable), 60-minute travel
  limit by default.
- Existing station polygon/point/line matcher and conservative fallback,
  pedestrian audit, `stop_area` and pedestrian transfers.
- Reuses existing PBFs and graph caches when stop fingerprints match. If
  fewer/more stops are selected after source validation, the generator
  automatically rebuilds affected graph caches.

## Install and run

Extract the flat archive in the project directory:

```
tar -xf transport-map-osmlab-v0.12.2.tar
./build-transport-map.py --self-test
./build-transport-map.py --report-json report.json
```

Inspect one route without recalculating the city graph:

```
./build-transport-map.py --inspect-route-ref '356к'
```

For a machine-readable inspection:

```
./build-transport-map.py --inspect-route-ref '356к' --report-json 356k.json
```

Audit all rail-station access:

```
./build-transport-map.py --audit-rail-access rail-access-audit.json
```

The inspect command is based on parsed OSM relations, not a timetable or
real-time route registry. If `356к` remains as a train, inspect its returned
relation IDs and `quarantined_route_way_conflicts`; source tagging may be
incorrect or the relation may have no route ways to validate.

## Model limitations

**This release does not use GTFS schedules.** Mode speeds and waiting times
are heuristic. Route geometries are used for distance only when validated;
there is no guarantee that a published vehicle service follows all present
OSM relations or that a specific departure exists. An OSM route relation is
not equivalent to a GTFS trip. A connection to a pedestrian graph is not a
proof of public, level-correct, bidirectional access. The existing station
links marked as estimated remain estimated.

The complete RU-MOW/RU-MOS PBF data were not available during development
of this release. Offline tests and a tiny end-to-end GeoJSON/HTML fixture
passed, including JavaScript syntax and route-continuation regressions.
The exact effect on route 356к and full-Moscow timings must be verified on
local real PBF files. Do not interpret the model as an OTP/R5-equivalent
schedule-based accessibility calculation.

## Troubleshooting

Check `report.json`:

- `extraction_audit.route_master_mode_conflicts`
- `extraction_audit.quarantined_route_way_conflicts`
- `travel_model.geometry_based_edges`
- `travel_model.estimated_distance_edges`
- `rail_access_quality` and `rail_boarding_access`

A route filtered by the type-validation check is intentionally omitted
rather than assigned an unsupported transportation mode. Treat the listed
OSM relation IDs as candidates for source-data inspection, not as automatic
proof of bad OSM mapping.
