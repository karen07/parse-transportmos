# Moscow Transport Map v0.7.7 - Moskvo-rechye station access test

Single readable `build-transport-map.py` creates a standalone `routes.html`.
The fast v0.7.3-based RAPTOR, walking wave, Canvas, 7 transport modes,
Reset, and profiler are unchanged.

## Why this update exists

The v0.7.6 OSM inspection confirmed that both railway stops at Moskvo-rechye
are attached to an 11-node walking component. Four adjoining OSM ways form a
chain but were rejected because of `access=private`:

- `149737448`, `149737460`, `149737453`: `highway=footway`
- `160799180`: `highway=steps`, `bridge=yes`, `layer=1`

These are NOT evidence that the chain is publicly accessible. The explicit
CLI switch below is an EXPERIMENT to determine whether excluding these ways
is the cause of the missing walking wave. Do not use an experimental map as
an authoritative walking or accessibility guide. No other private ways are
permitted and `foot=no` is never overridden. No link is invented across rail
tracks, fences, roads, or unrelated OSM nodes.

## Quick verification (recommended FIRST)

Run from your existing `parse-transportmos` directory, after replacing the
script with this version (do NOT delete your caches):

```sh
./build-transport-map.py \
    --allow-moskvorechye-private \
    --check-moskvorechye-only
```

This builds or reuses a separate pedestrian cache and checks weak graph
connectivity from OSM railway platforms to nearby surface transit stops.
The first run must process the complete OSM pedestrian input and may take
several minutes. Subsequent checks load the new cache quickly. The check
exits with code 0 for PASS, code 2 for FAIL; check the printed lines:

```text
[station] permitted private OSM ways with graph edges: [...]
[station] platform: n1987940555 component: ... nodes: ...
[station] platform: n1987940556 component: ... nodes: ...
[station] nearby street stops: ... connected to platforms: ...
[station] CONNECTION PASS
```

A PASS is a topological *weak-connectivity* check, not proof that the
station has a public entry, or that directed walking is possible. If it
fails, the four ways are insufficient; do not add guessed connections.
Share the `[station]` log to investigate the next missing piece.

## Build the full map in experimental mode

If the check passes, run:

```sh
./build-transport-map.py \
    --allow-moskvorechye-private \
    --report-json report.json
```

The output is `routes.html` alongside the script. After building the walking
transfer graph, read the additional directional check:

```text
[station] Directed walking transfers FROM n1987940555 ...
[station] Directed walking transfers FROM n1987940556 ...
[station] DIRECTED CONNECTION PASS
```

That test confirms actual outward directed pedestrian paths within the 15 min
walking transfer radius (still not whether use of private ways is lawful).

The `--allow-moskvorechye-private` switch must also be supplied for every
subsequent build that should use the experimental graph. Without it, the
previous safe graph and caches are used, unchanged.

## Cache isolation

The normal caches are preserved, including:

```text
cache/moscow-pedestrian-v3.json.gz
cache/moscow-walk-transfers-v2.json.gz
```

Test mode writes new, separate files:

```text
cache/moscow-pedestrian-v4-moskvorechye-test.json.gz
cache/moscow-walk-transfers-v3-moskvorechye-test.json.gz
```

The test walking cache detects changes in the original PBF timestamp and
size. To force rebuilding the experimental graph, add `--rebuild-walking`.
The walking transfer cache is also rebuilt when walking graph input changes.

## What was changed

- Local, opt-in `access=private` exception for exactly 4 known OSM way IDs,
  only if the way has a pedestrian `highway`, with no explicit `foot=no`.
- Prefer these station footways over a nearby service road when attaching
  the two specific Moskvo-rechye railway stops, but only if within 12 m of
  the closest candidate, and still within the established snap limit.
- Report post-build station components and directed train-to-street transfers.
- No changes to the high-performance routing, viewport drawing, or UI.

## Validation and limitations

Checks: Python compile, generated HTML JavaScript syntax, ASCII and 100-char
line limits. A synthetic network verified that disabling the override keeps
the platform isolated, enabling the exact four ways joins it to an existing
street path by shared OSM nodes, and `foot=no` remains excluded.

The full Moscow PBF is not in the test environment. Whether these exact ways
join the real street graph and whether they should be accessible to ordinary
passengers still need checking on the user's OSM source and in the field.
