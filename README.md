# Moscow Transport Map v0.8.0 - Compact Walking Graph and Station Access

One executable Python file builds one standalone `routes.html`. The two
regional OpenStreetMap extracts remain OSMlab RU-MOW (Moscow city) and
RU-MOS (Moscow Oblast). No external `osmium-tool` executable is required.

## Installation

Use your existing Python environment with pyosmium 4.x. For a new virtual
environment, install `osmium>=4.0` through pip. The executable Python script
already has mode 755 in the release tarball.

```sh
tar -xf transport-map-osmlab-v0.8.0.tar
./build-transport-map.py --report-json report.json
```

Do not use `--update` unless you want to redownload both OSMlab region PBFs.
The normal build reuses the regional source and transport caches. The first
v0.8.0 build automatically regenerates the pedestrian and walking-transfer
caches because the pedestrian topology changed.

## Compact HTML payload

- The serialized walking graph uses signed delta-varints for consecutive
  X/Y coordinates and edge endpoints; lengths remain integer decimeters.
- The existing 0.1 m precision, edge indices and directions are preserved.
- The packed arrays are gzip-compressed and Base64-embedded in the single
  HTML file. No extra requests or local HTTP server are needed.
- JavaScript decodes the packed arrays when opening the page and reuses the
  original route engine and renderer.
- The complete pedestrian graph is kept. No way is dropped just to save
  bytes, and no claimed percentage reduction is guaranteed for real OSM.

## Station access model

- During the existing OSM node scan, collect tagged subway/station entrances
  and `public_transport=stop_area` relations.
- When a rail boarding stop and entrance share one explicit `stop_area`,
  attach the stop to every usable street-connected entrance, not just one
  nearby platform-side segment.
- Street-side entrances use exact shared OSM nodes when possible; otherwise
  a conservative 12 m geometric snap is attempted.
- Add routing-only indoor links with estimated costs, never as actual street
  geometry. `entrance=exit` and `entrance=entrance` are directional.
- When no entrance in the same `stop_area` is usable, retain the older
  attachment as a fallback rather than inventing an arbitrary passage.

This is a preliminary station-access model, not a guarantee of true indoor
routing. An OSM stop_area records station membership, not exact indoor path
geometry, access permissions or passage times. Some stations may still lack
proper OSM entrance mapping. For those cases use `--diagnose-station` with
`--diagnose-only` and inspect the generated GeoJSON before changing OSM
access tags or introducing any exceptional connection. The existing test-only
private-way option is unchanged and remains off by default.

The console reports how many `stop_area` relations matched, how many rail
boarding stops gained street access and how many indoor links were made.

## Cache and tests

Existing OSMlab sources and transport caches are retained. The updated
walking cache version invalidates older pedestrian graphs; the walking
transfer cache is then rebuilt from the new graph. The compact HTML format
does not change the structure of on-disk pedestrian caches.

To inspect an individual station after the rebuild:

```sh
./build-transport-map.py --diagnose-station \
    "$(printf '\u0414\u0430\u0432\u044b\u0434\u043a\u043e\u0432\u043e')" \
    --diagnose-only
```

The Python source and README contain ASCII only and Python source lines are
at most 100 characters. Python compilation and synthetic HTML/JavaScript
and station-access tests were run. Full `black`/`mdformat` validation was
not possible in the build environment because those programs are absent.

## Known limitations and next steps

- No real Moscow PBF run was possible in the release environment. Test the
  station cases at Moskvo-rechye, Nagatinskiy Zaton, Davydkovo and
  Akademicheskaya against your own OSMlab caches.
- A strict 10 km buffer around the real MKAD boundary is not implemented in
  this release. The area must be derived from an authoritative OSM polygon
  and route directions must be clipped without creating artificial jumps.
- Browser memory usage is not reduced proportionally to the HTML file size,
  because the page still reconstructs the full walk graph after decoding.
