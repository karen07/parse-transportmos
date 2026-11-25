"""Site builder for the Moscow transport map."""

import base64
import gzip
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from .common import PROJECT_VERSION
from .i18n import load_catalogs, publish_catalogs, render_default, wrap_html_lines

# Standalone HTML/CSS/JavaScript source
# Web UI lives in the web/ source tree. No JavaScript/CSS is embedded in Python.
WEB_SOURCE_DIR = Path(__file__).resolve().parents[1] / "web"


# Pack 0.1 m coordinates and edge lengths into 32-bit integers. This
# changes only the HTML payload; build caches retain the readable graph.
# A 2.3M-node graph otherwise generates tens of millions of JSON digits.
def encode_unsigned_varint(output, value):
    if value < 0 or value >= 2**32:
        raise ValueError("Walking graph value exceeds unsigned 32-bit range")
    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)


def encode_signed_varint(output, value):
    if not -(2**31) <= value < 2**31:
        raise ValueError("Walking graph coordinate/delta exceeds 32 bits")
    encode_unsigned_varint(output, (value << 1) ^ (value >> 31))


def pack_walking_payload(data):
    walking = data["walking"]
    vertices = bytearray()
    arcs = bytearray()
    previous_x = 0
    previous_y = 0
    for x, y in walking["nodes"]:
        next_x = round(x * 10)
        next_y = round(y * 10)
        encode_signed_varint(vertices, next_x - previous_x)
        encode_signed_varint(vertices, next_y - previous_y)
        previous_x, previous_y = next_x, next_y
    previous_a = 0
    for a, b, meters, direction in walking["edges"]:
        encode_signed_varint(arcs, a - previous_a)
        encode_signed_varint(arcs, b - a)
        encode_unsigned_varint(arcs, round(meters * 10))
        arcs.append(direction)
        previous_a = a
    encoded = {
        "format": "delta-varint-dm-v1",
        "node_count": len(walking["nodes"]),
        "edge_count": len(walking["edges"]),
        "nodes": base64.b64encode(vertices).decode("ascii"),
        "edges": base64.b64encode(arcs).decode("ascii"),
    }
    return {**data, "walking": encoded}


def pack_transfers_payload(graph):
    """Encode CSR transfers without changing the cached graph or distances."""
    offsets = graph["offsets"]
    targets = graph["to"]
    meters = graph["meters"]
    if len(offsets) < 1 or offsets[0] != 0:
        raise ValueError("Transfer offsets must begin at zero")
    if offsets[-1] != len(targets) or len(targets) != len(meters):
        raise ValueError("Transfer CSR arrays have inconsistent lengths")
    out = bytearray()
    stop_count = len(offsets) - 1
    for source in range(stop_count):
        first, last = offsets[source : source + 2]
        if not 0 <= first <= last <= len(targets):
            raise ValueError("Transfer CSR offsets are not sorted")
        encode_unsigned_varint(out, last - first)
        previous = 0
        for index in range(first, last):
            target = targets[index]
            if not previous <= target < stop_count:
                raise ValueError("Transfer targets must be sorted by stop")
            encode_unsigned_varint(out, target - previous)
            previous = target
            distance = meters[index]
            centimeters = round(distance * 100)
            if abs(centimeters / 100 - distance) > 0.0000001:
                raise ValueError("Transfer distance would lose precision")
            encode_unsigned_varint(out, centimeters)
    return {
        "format": "csr-delta-varint-cm-v1",
        "stop_count": stop_count,
        "arc_count": len(targets),
        "arcs": base64.b64encode(out).decode("ascii"),
    }


def pack_route_geometry_payload(data):
    """Pack line geometry to 1e-7 degrees, about a centimeter at Moscow."""
    packed_routes = {}
    encoded = bytearray()
    count = 0
    for key, route in data["routes"].items():
        compact_route = dict(route)
        compact_route.pop("stops", None)
        compact_route.pop("network", None)
        compact_route.pop("roundtrip", None)
        directions = []
        for direction in route["directions"]:
            compact_direction = dict(direction)
            compact_direction.pop("geometry_source", None)
            # Keep roundtrip=yes! The browser needs it for topology-based
            # circle continuation; the old compact payload dropped it.
            geometry = direction.get("geometry") or {}
            kind = geometry.get("type")
            if kind in ("LineString", "MultiLineString"):
                parts = (
                    [geometry["coordinates"]]
                    if kind == "LineString"
                    else geometry["coordinates"]
                )
                encoded.append(1 if kind == "LineString" else 2)
                encode_unsigned_varint(encoded, len(parts))
                for line in parts:
                    encode_unsigned_varint(encoded, len(line))
                    previous_lon = 0
                    previous_lat = 0
                    for lon, lat in line:
                        next_lon = round(lon * 10000000)
                        next_lat = round(lat * 10000000)
                        encode_signed_varint(encoded, next_lon - previous_lon)
                        encode_signed_varint(encoded, next_lat - previous_lat)
                        previous_lon, previous_lat = next_lon, next_lat
                compact_direction["geometry"] = count
                count += 1
            directions.append(compact_direction)
        compact_route["directions"] = directions
        packed_routes[key] = compact_route
    return packed_routes, {
        "format": "route-delta-varint-e7-v1",
        "count": count,
        "data": base64.b64encode(encoded).decode("ascii"),
    }


def pack_html_payload(data):
    """Strip build-only indexes and encode routes, walking, and transfers."""
    routes, geometries = pack_route_geometry_payload(data)
    trimmed = {
        **data,
        "routes": routes,
        "route_geometries": geometries,
        "walk_transfers": pack_transfers_payload(data["walk_transfers"]),
    }
    trimmed.pop("grid", None)
    trimmed.pop("grid_size", None)
    return pack_walking_payload(trimmed)


# Static page generator (HTML + CSS + JS + worker + compressed payload)
def write_atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_boot_svg(cache_dir, output_path):
    """Publish only the final SVG; keep intermediate road geometry in the cache."""
    from .boot_svg import render_svg

    roads_path = Path(cache_dir) / "moscow-major-roads.json"
    svg_path = Path(output_path).with_name("moscow-major-roads.svg")
    if roads_path.is_file() or not svg_path.is_file():
        write_atomic_bytes(svg_path, render_svg(roads_path).encode("ascii"))
    return svg_path


def render_page(
    output_path, initial_walk_minutes, walk_speed, initial_opacity, catalogs=None
):
    """Render and validate HTML without reading or building any transport data."""
    if catalogs is None:
        catalogs = load_catalogs()
    base = Path(output_path).stem
    js_filename = base + ".js"
    css_filename = base + ".css"
    config_filename = base + "-config.json"
    initial_walk_minutes = max(1.0, min(15.0, initial_walk_minutes))
    initial_circle_opacity = max(0.0, min(100.0, initial_opacity))
    html = (WEB_SOURCE_DIR / "page.html").read_text(encoding="ascii")
    html = render_default(html, catalogs, walk_speed)
    replacements = {
        "@@VERSION@@": PROJECT_VERSION,
        "@@LANGUAGES@@": " ".join(sorted(catalogs)),
        "@@PAGE_JS@@": quote(js_filename),
        "@@PAGE_CSS@@": quote(css_filename),
        "@@CONFIG_JSON@@": quote(config_filename),
        "@@INITIAL_WALK_MINUTES@@": f"{initial_walk_minutes:.0f}",
        "@@INITIAL_OPACITY@@": f"{initial_circle_opacity:.0f}",
        "@@WALK_SPEED@@": f"{walk_speed:.0f}",
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    html = wrap_html_lines(html)
    # Inline raw source only after wrapping markup. Never reflow JavaScript/CSS.
    for token, source in (
        ("/* @@BOOT_CSS@@ */", "boot-loader.css"),
        ("/* @@LANGUAGE_JS@@ */", "language.js"),
        ("/* @@BOOT_THEME_JS@@ */", "boot-theme.js"),
    ):
        content = (WEB_SOURCE_DIR / source).read_text(encoding="ascii")
        html = html.replace(token, "\n" + content.rstrip() + "\n")
    if re.search(r"@@[A-Z_]+@@", html):
        raise ValueError("Unresolved HTML template placeholder")
    # Site CSP has script-src-attr 'none': keep inline event handlers out of HTML.
    if re.search(r"\son[a-z]+\s*=", html, flags=re.IGNORECASE):
        raise ValueError("Generated HTML contains CSP-blocked inline event handlers")
    result = (
        "\n".join(line.rstrip() for line in html.splitlines() if line.strip()) + "\n"
    )
    long_lines = [
        (i, len(line), line)
        for i, line in enumerate(result.splitlines(), 1)
        if len(line) > 100
    ]
    if long_lines:
        details = "\n".join(
            f"  {i}: {length} chars: {line}" for i, length, line in long_lines[:10]
        )
        raise ValueError(f"Generated HTML lines exceed 100:\n{details}")
    return result


def build_html(
    data,
    initial_walk_minutes,
    walk_speed,
    initial_circle_opacity,
    output_path,
    reuse_data=False,
    cache_dir=Path("build"),
):
    if not reuse_data:
        html_started = time.monotonic()
        print("[build] START JSON serialization", flush=True)
        raw_payload = json.dumps(
            pack_html_payload(data),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        print(
            "[build] DONE JSON serialization:",
            f"{time.monotonic()-html_started:.3f}s",
            flush=True,
        )
        html_started = time.monotonic()
        print("[build] START gzip compression", flush=True)
        compressed_payload = gzip.compress(raw_payload, compresslevel=9, mtime=0)
        print(
            "[build] DONE gzip compression:",
            f"{time.monotonic()-html_started:.3f}s",
            flush=True,
        )
        print(
            "external data:",
            f"{len(raw_payload) / (1024 * 1024):.1f} MiB raw ->",
            f"{len(compressed_payload) / (1024 * 1024):.1f} MiB gzip",
        )

    origin_lon, origin_lat = data["origin"]
    grid_size = data["grid_size"]

    initial_walk_minutes = max(
        1.0,
        min(15.0, initial_walk_minutes),
    )

    initial_circle_opacity = max(
        0.0,
        min(100.0, initial_circle_opacity),
    )

    catalogs = load_catalogs()

    output_path = Path(output_path)
    base = output_path.stem
    data_filename = base + ".json.gz"
    js_filename = base + ".js"
    css_filename = base + ".css"
    data_worker_filename = base + "-data-worker.js"
    maplibre_worker_filename = base + "-maplibre-worker.js"
    config_filename = base + "-config.json"
    from .major_roads import (
        CENTER_LAT,
        CENTER_LON,
        METERS_PER_DEGREE_LON,
        VIEW_X_METERS,
    )

    # The language bootstrap and critical CSS are inlined before first paint.
    config = {
        "version": PROJECT_VERSION,
        "boot_view": {
            "lat": CENTER_LAT,
            "lon": CENTER_LON,
            "width_degrees": 2 * VIEW_X_METERS / METERS_PER_DEGREE_LON,
        },
        "data_file": data_filename,
        "data_worker_file": data_worker_filename,
        "maplibre_worker_file": maplibre_worker_filename,
        "walk_speed": walk_speed,
        "grid_size": grid_size,
        "origin_lon": origin_lon,
        "origin_lat": origin_lat,
        "infer_through_service": data.get("through_service_policy") == "estimated",
        "initial_walk_minutes": initial_walk_minutes,
        "initial_opacity": initial_circle_opacity,
    }
    result = render_page(
        output_path, initial_walk_minutes, walk_speed, initial_circle_opacity, catalogs
    )
    publish_boot_svg(Path(cache_dir), output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    icon_assets = Path(__file__).resolve().parents[1] / "assets"
    for name in (
        "favicon.svg",
        "favicon.ico",
        "favicon-32.png",
        "apple-touch-icon.png",
    ):
        write_atomic_bytes(
            output_path.with_name(name), (icon_assets / name).read_bytes()
        )
    if not reuse_data:
        write_atomic_bytes(output_path.with_name(data_filename), compressed_payload)
    write_atomic_bytes(
        output_path.with_name(config_filename),
        (json.dumps(config, ensure_ascii=True, indent=4) + "\n").encode("ascii"),
    )
    static_assets = {
        js_filename: "routes.js",
        css_filename: "routes.css",
        data_worker_filename: "routes-data-worker.js",
        "boot-loader.js": "boot-loader.js",
        "i18n.js": "i18n.js",
    }
    for destination, source in static_assets.items():
        content = (WEB_SOURCE_DIR / source).read_bytes()
        write_atomic_bytes(output_path.with_name(destination), content)
    for theme in ("light", "dark"):
        filename = theme + ".json"
        content = (WEB_SOURCE_DIR / "styles" / filename).read_bytes()
        write_atomic_bytes(output_path.parent / "styles" / filename, content)
    publish_catalogs(output_path.parent / "i18n", catalogs, write_atomic_bytes)
    # Older releases copied this stylesheet, but it is now inlined for FCP.
    output_path.with_name("boot-loader.css").unlink(missing_ok=True)
    # Prefer to vendor the official matching CSP worker once at build time.
    # If unavailable, fall back to a same-origin shim importing from the
    # same unpkg origin that already serves MapLibre's main JS bundle.
    csp_worker_path = output_path.with_name(maplibre_worker_filename)
    if not csp_worker_path.is_file() or csp_worker_path.stat().st_size < 100000:
        url = "https://unpkg.com/maplibre-gl@4.7.1/dist/" "maplibre-gl-csp-worker.js"
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "transport-map-static-builder"}
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                content = response.read(2 * 1024 * 1024 + 1)
            if not 100000 < len(content) < 2 * 1024 * 1024:
                raise ValueError("Unexpected MapLibre CSP worker size")
            write_atomic_bytes(csp_worker_path, content)
            print("[build] vendored MapLibre 4.7.1 CSP worker", flush=True)
        except (OSError, ValueError, urllib.error.URLError) as error:
            print("[build] MapLibre worker download unavailable:", error)
            print("[build] using CDN-backed same-origin shim", flush=True)
            maplibre_shim = (
                "// Same-origin MapLibre CSP Worker for worker-src 'self'.\n"
                'importScripts("https://unpkg.com/maplibre-gl@4.7.1/'
                'dist/maplibre-gl-csp-worker.js");\n'
            )
            write_atomic_bytes(csp_worker_path, maplibre_shim.encode("ascii"))
    print(
        "[build] web assets:",
        ", ".join(
            (
                data_filename,
                js_filename,
                css_filename,
                data_worker_filename,
                maplibre_worker_filename,
            )
        ),
        flush=True,
    )
    # Preview is available on the same page via ?boot-preview=1.
    output_path.with_name("boot-preview.html").unlink(missing_ok=True)
    return result
