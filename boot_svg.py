#!/usr/bin/env python3
"""Prepare a tiny, static first-paint SVG from generated OSM major roads.

This emits SVG geometry, not application HTML/CSS/JavaScript.  The browser
can paint it before downloading any script, stylesheet or OSM dataset.
"""

import json
import math
from pathlib import Path

VIEW_WIDTH = 800
VIEW_HEIGHT = 400
MAX_PATH_CHARS = 12000


def distance_to_line(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / max(
        dx * dx + dy * dy, 1e-12
    )
    t = max(0.0, min(1.0, t))
    return math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dy)


def simplify_screen(points, epsilon=1.2):
    if len(points) <= 2:
        return points
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        best, chosen = 0.0, -1
        for idx in range(first + 1, last):
            dist = distance_to_line(points[idx], points[first], points[last])
            if dist > best:
                best, chosen = dist, idx
        if best > epsilon:
            keep.add(chosen)
            stack.append((first, chosen))
            stack.append((chosen, last))
    return [points[idx] for idx in sorted(keep)]


def svg_path(data, max_chars=MAX_PATH_CHARS):
    """Bound HTML bytes while keeping full connected paths, never fake gaps."""
    if data.get("format") != "moscow-major-roads-v1":
        raise ValueError("Unsupported major road geometry")
    mx, my = data["extent"]
    if not all(math.isfinite(v) and v > 0 for v in (mx, my)):
        raise ValueError("Invalid road extents")
    scale = min(760 / (2 * mx), 376 / (2 * my))
    candidates = []
    for kind, coords in data["paths"]:
        if len(coords) < 4 or len(coords) % 2:
            continue
        points = [
            (
                round(VIEW_WIDTH / 2 + coords[i] * scale),
                round(VIEW_HEIGHT / 2 + coords[i + 1] * scale),
            )
            for i in range(0, len(coords), 2)
        ]
        points = simplify_screen(points)
        # Drawings below 2 screen pixels are not meaningful in a loader.
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        if length < 2:
            continue
        tokens = [f"M{points[0][0]} {points[0][1]}"]
        tokens.extend(f"{x} {y}" for x, y in points[1:])
        line = " ".join(tokens)
        candidates.append((kind, length, line))

    # Protect Moscow's rings and the longest radials under a strict byte cap.
    # Never truncate coordinates halfway through a path.
    candidates.sort(key=lambda item: (item[0] not in (0, 1), -item[1]))
    accepted = []
    size = 0
    for _, _, line in candidates:
        if size + len(line) + 1 > max_chars:
            continue
        accepted.append(line)
        size += len(line) + 1
    return " ".join(accepted)


def wrapped_svg_path(path):
    """Whitespace-separated tokens on <= 100-column lines."""
    # A line break in the middle of an SVG path attribute is whitespace.
    words = path.split()
    lines = []
    current = "        "
    for word in words:
        if len(current) + len(word) + 1 > 92:
            lines.append(current.rstrip())
            current = "        " + word
        else:
            current += (" " if current.strip() else "") + word
    if current.strip():
        lines.append(current.rstrip())
    return "\n".join(lines)


def render_inline_svg(road_json):
    road_json = Path(road_json)
    geometry = ""
    if road_json.is_file():
        data = json.loads(road_json.read_text(encoding="ascii"))
        geometry = wrapped_svg_path(svg_path(data))
    return (
        '<svg id="boot-roads" viewBox="0 0 800 400"\n'
        '    preserveAspectRatio="xMidYMid meet" aria-hidden="true">\n'
        '    <path id="boot-road-path" d="\n'
        + geometry
        + '\n    " fill="none" stroke="#39465f" stroke-width="1.55"\n'
        '    stroke-linecap="round" stroke-linejoin="round"/>\n'
        "</svg>"
    )
