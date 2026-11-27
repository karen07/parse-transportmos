"""Transfers for the Moscow transport map."""

import gzip
import hashlib
import heapq
import json
import math
import time
from array import array
from collections import OrderedDict, defaultdict
from pathlib import Path

from .common import (
    SURFACE_MODES,
    TRANSFERS_CACHE_NAME,
    TRANSFERS_TEST_CACHE_NAME,
    cache_implementation,
)
from .walking import MOSKVORECHYE_PLATFORM_IDS

# Offline walking transfer graph: directed CSR with network shortest paths.
# This is distinct from the full OSM graph needed for on-map coverage.


def merge_interchange_hints(offsets, targets, meters, hints, stop_count):
    """Merge organizational interchange estimates into directed transfer CSR.

    Existing measured pedestrian routes always win over estimated ones. Only
    missing transfer targets are added; hints do not alter walking geometry.
    """
    if not hints:
        return offsets, targets, meters
    by_source = defaultdict(list)
    for a, b, distance, _relation_id in hints:
        if 0 <= a < stop_count and 0 <= b < stop_count and a != b:
            by_source[a].append((b, distance))
    new_offsets = [0]
    new_targets = []
    new_meters = []
    inserted = 0
    for source in range(stop_count):
        current = dict(
            zip(
                targets[offsets[source] : offsets[source + 1]],
                meters[offsets[source] : offsets[source + 1]],
            )
        )
        for target, distance in by_source.get(source, ()):
            if target not in current:
                current[target] = distance
                inserted += 1
        for target, length in sorted(current.items()):
            new_targets.append(target)
            new_meters.append(length)
        new_offsets.append(len(new_targets))
    print("[station] estimated interchange arcs added to transfers:", inserted)
    return new_offsets, new_targets, new_meters


def build_walk_transfers(walking, stops, radius_m):
    started = time.monotonic()
    count = len(walking["nodes"])
    stop_nodes = defaultdict(list)
    for index, stop in enumerate(stops):
        node = stop.get("walk_node", -1)
        if isinstance(node, int) and 0 <= node < count:
            stop_nodes[node].append(index)

    # Directed adjacency exactly matches the browser's walking CSR.
    degree = array("I", [0]) * count
    for a, b, length, direction in walking["edges"]:
        if direction not in (3, 13):
            degree[a] += 1
        if direction not in (2, 12):
            degree[b] += 1
    offsets = array("I", [0]) * (count + 1)
    for node in range(count):
        offsets[node + 1] = offsets[node] + degree[node]
    to = array("I", [0]) * offsets[-1]
    weight = array("f", [0.0]) * offsets[-1]
    cursor = array("I", offsets[:-1])
    for a, b, length, direction in walking["edges"]:
        if direction not in (3, 13):
            slot = cursor[a]
            to[slot] = b
            weight[slot] = length
            cursor[a] += 1
        if direction not in (2, 12):
            slot = cursor[b]
            to[slot] = a
            weight[slot] = length
            cursor[b] += 1
    del degree, cursor

    result_offsets = [0]
    result_to = []
    result_meters = []
    # A search is shared by all stops attached to the same graph vertex.
    # Bounded reuse for stations sharing an identical attachment node.
    reached_by_node = OrderedDict()
    for stop_index, stop in enumerate(stops):
        source = stop.get("walk_node", -1)
        if not isinstance(source, int) or source < 0 or source >= count:
            result_offsets.append(len(result_to))
            continue
        if source in reached_by_node:
            reached = reached_by_node[source]
            reached_by_node.move_to_end(source)
        else:
            best = {source: 0.0}
            heap = [(0.0, source)]
            reached = []
            while heap:
                distance, node = heapq.heappop(heap)
                if distance > radius_m + 0.00001:
                    break
                if distance > best[node] + 0.00001:
                    continue
                for other_stop in stop_nodes.get(node, ()):
                    reached.append((other_stop, round(distance, 2)))
                for edge in range(offsets[node], offsets[node + 1]):
                    next_node = to[edge]
                    next_distance = distance + weight[edge]
                    if next_distance > radius_m + 0.00001:
                        continue
                    if next_distance + 0.00001 >= best.get(next_node, math.inf):
                        continue
                    best[next_node] = next_distance
                    heapq.heappush(heap, (next_distance, next_node))
            reached.sort()
            reached_by_node[source] = reached
            if len(reached_by_node) > 256:
                reached_by_node.popitem(last=False)
        for target, distance in reached:
            result_to.append(target)
            result_meters.append(distance)
        result_offsets.append(len(result_to))
        if (stop_index + 1) % 1000 == 0:
            elapsed = time.monotonic() - started
            print(
                "  transfer graph:",
                stop_index + 1,
                "/",
                len(stops),
                "stops;",
                len(result_to),
                "arcs;",
                f"{elapsed:.1f}s",
            )

    result_offsets, result_to, result_meters = merge_interchange_hints(
        result_offsets,
        result_to,
        result_meters,
        walking.get("interchange_hints", ()),
        len(stops),
    )
    print(
        "walking transfer graph:",
        len(result_to),
        "arcs in",
        f"{time.monotonic()-started:.1f}s",
    )
    return {"offsets": result_offsets, "to": result_to, "meters": result_meters}


def check_moskvorechye_transfers(transfers, stops):
    """Verify directed reachable street stops, not just weak connectivity."""
    count = 0
    for i, rail in enumerate(stops):
        if rail["id"] not in MOSKVORECHYE_PLATFORM_IDS:
            continue
        matches = []
        start, end = transfers["offsets"][i : i + 2]
        for pos in range(start, end):
            other = stops[transfers["to"][pos]]
            if not (set(other.get("modes", ())) & SURFACE_MODES):
                continue
            if math.hypot(rail["x"] - other["x"], rail["y"] - other["y"]) > 700.0:
                continue
            matches.append((other["id"], round(transfers["meters"][pos])))
        count += len(matches)
        print(
            "[station] Directed walking transfers FROM",
            rail["id"],
            "to nearby surface stops:",
            len(matches),
            flush=True,
        )
        for osm_id, meters in matches[:5]:
            print("[station]   ->", osm_id, meters, "m", flush=True)
    if count == 0:
        print(
            "[station] WARNING: still no directed platform-to-street path!", flush=True
        )
    else:
        print("[station] DIRECTED CONNECTION PASS", flush=True)
    return count > 0


def load_or_build_walk_transfers(
    cache_dir,
    walking_source,
    walking,
    stops,
    radius_m,
    rebuild=False,
    allow_private_station_ways=False,
):
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        TRANSFERS_TEST_CACHE_NAME
        if allow_private_station_ways
        else TRANSFERS_CACHE_NAME
    )
    path = cache_dir / filename
    source = Path(walking_source)
    stamp = source.stat() if source.is_file() else None
    source_key = (
        [str(source.resolve()), stamp.st_size, stamp.st_mtime_ns] if stamp else None
    )
    fingerprint = hashlib.sha256()
    for stop in stops:
        fingerprint.update(
            (str(stop["id"]) + ":" + str(stop.get("walk_node", -1)) + ";").encode(
                "utf-8"
            )
        )
    metadata = {
        "implementation": cache_implementation(),
        "stop_fingerprint": fingerprint.hexdigest(),
        "source": source_key,
        "radius_m": radius_m,
        "nodes": len(walking["nodes"]),
        "edges": len(walking["edges"]),
    }
    if path.is_file() and not rebuild:
        with gzip.open(path, "rt", encoding="ascii") as stream:
            cached = json.load(stream)
        if all(cached.get(key) == value for key, value in metadata.items()):
            graph = cached["transfers"]
            if (
                len(graph.get("offsets", ())) == len(stops) + 1
                and graph["offsets"][-1] == len(graph.get("to", ()))
                and len(graph["to"]) == len(graph.get("meters", ()))
            ):
                print(f"Using cached walking transfers: {path}")
                return graph
        print("Walking transfer cache differs; rebuilding...")
    graph = build_walk_transfers(walking, stops, radius_m)
    content = {**metadata, "transfers": graph}
    temp = path.with_suffix(".tmp.gz")
    with gzip.open(temp, "wt", encoding="ascii", compresslevel=6) as stream:
        json.dump(content, stream, ensure_ascii=True, separators=(",", ":"))
    temp.replace(path)
    print(f"Saved walking transfer cache: {path}")
    return graph
