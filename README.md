# Moscow transport reachability v0.7.3

One readable `build-transport-map.py` generates a single `routes.html`.
This is a walking-coverage performance release based on the real v0.7.1
profile: routing ended in approximately 0.65 s, but the last canvas
completed only after 85.6 s.

## Generate

```sh
./build-transport-map.py --report-json report.json
```

The transport v4 cache, pedestrian v2 cache, and walk-transfers v1 cache
are compatible. No new data-cache format is required.

## Changes

- Wait for RAPTOR (previously ~0.25 s) to collect all walk sources before
  starting one multi-source pedestrian wave. This prevents late sources
  from repeatedly improving previously visited vertices.
- Track four transfer-priority classes with dense numeric budgets and settled
  flags, and use a typed binary heap without an allocation per insertion.
- Do not accumulate millions of global intermediate line segments. Only
  enqueue visible OSM edges for Canvas; paint each edge at most twice per tier
  in the initial pass (once per settled endpoint).
- Rebuild a new viewport from saved walking distances and the existing OSM
  spatial grid on pan/zoom, without rerunning RAPTOR or walking Dijkstra.
- Cache Web Mercator screen coordinates for vertices per map transform.
- Batch opaque color-mask composition approximately every 70 ms; retain
  one 40%-opacity output layer with the same transfer-priority order.
- Preserve progressive transit lines/stops, the detailed v0.7.1 report,
  Reset, route details, and all mode switches.

There is one deliberate visual change: walking coverage starts immediately
*after* routing finishes, rather than while RAPTOR is still discovering
stops. This sacrifices ~0.25 s of early walking animation in exchange for a
much smaller total search and render workload.

## Diagnostics

Open DevTools Console after selecting a starting point. Watch for
`[map] DONE Walking coverage wave`, `[map] FULLY IDLE`, and the final profile:

```js
copy(JSON.stringify(window.transportLastProfile, null, 2))
```

If a run is still active:

```js
copy(JSON.stringify(window.transportCurrentProfile(), null, 2))
```

Compare `totalWallMs`, the `Walking coverage wave` phase, `final.wave.cpuMs`,
`final.wave.settled`, `final.wave.segments`, `cpuMs.coveragePaint`, and
`cpuMs.coverageComposite` against v0.7.1 with the same origin, zoom, and
controls. The initial snapshot can legitimately show `Walking coverage wave`
after transport has completed.

## Verification and limitations

The generated HTML is ASCII and the Python/JS source stays within 100
characters per line. A test fixture builds successfully; wave distances
match an independent algorithm on 40 randomized directed graphs.
The full Moscow PBF, live Leaflet UI with online CDN resources, and the
user's `format-go`/HTML Tidy pipeline were not benchmarked in this
execution environment. Do not assume any particular speedup until the
real Moscow profile is collected. Initial viewport rebuilding still requires
scanning the relevant grid cells, and very wide zoom levels can be costly.

## v0.7.3: faster final Canvas and smaller stops

- The multi-source walking wave computes only vertex budgets; it never
  enqueues per-edge drawing while the search is still running.
- Once the wave completes, a viewport query enumerates each OSM edge once,
  even if both endpoints or several stop sources reached that edge.
- Each of the four offscreen opaque tier masks is drawn in large,
  time-budgeted batches. Masks are composited into one translucent canvas
  every ~150 ms for preview and immediately at completion.
- The expensive 128 px dirty-tile tracking per segment is removed.
- The now-unused 2-entries-per-edge walking drawing index is not built.
- Stop-dot radius now depends on zoom (1.4 px at city scale).
- At zoom \<= 12, subpixel edges that land in the same screen cell are drawn
  only once per tier; the underlying OSM reachability remains unchanged.
- The profiler records separate viewport-indexing and Canvas-painting phases.
- Routing algorithms and the v4/v2/v1 graph caches are unchanged.

Check the same origin and settings as previous versions and compare `FULLY IDLE`,
`Coverage viewport index`, `Coverage Canvas painting`, `coveragePaint` and `coverageTinySkipped`.
Full Moscow dataset speed is not guaranteed without testing on that dataset.
