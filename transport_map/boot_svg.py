#!/usr/bin/env python3
"""Render grouped OSM road geometry for a self-contained first paint."""

import argparse
import json
import math
from pathlib import Path

VIEW_WIDTH = 900
VIEW_HEIGHT = 600
# Back-to-front order; the same stroke widths are used by the Canvas mask.
STYLES = {
    "tertiary": ("#29384f", 0.65),
    "secondary": ("#3b4b65", 0.95),
    "primary": ("#50647e", 1.35),
    "trunk": ("#657b97", 1.85),
    "motorway": ("#748ba5", 2.05),
    "chord": ("#7187a2", 2.0),
    "ring": ("#7e91aa", 1.95),
    "mkad": ("#879bb4", 2.2),
}


def distance_to_line(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / max(
        dx * dx + dy * dy, 1e-12
    )
    t = max(0.0, min(1.0, t))
    return math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dy)


def simplify_screen(points, epsilon=0.4):
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
            stack.extend(((first, chosen), (chosen, last)))
    return [points[idx] for idx in sorted(keep)]


def grouped_paths(data):
    """Use equal x/y scale, no arbitrary byte cap or partial path truncation."""
    if data.get("format") not in ("moscow-major-roads-v1", "moscow-major-roads-v2"):
        raise ValueError("Unsupported major road geometry")
    mx, my = data["extent"]
    if not all(math.isfinite(v) and v > 0 for v in (mx, my)):
        raise ValueError("Invalid road extents")
    scale = min(VIEW_WIDTH / (2 * mx), VIEW_HEIGHT / (2 * my))
    groups = {kind: [] for kind in STYLES}
    for kind, coords in data["paths"]:
        name = data["classes"][kind]
        if name not in groups or len(coords) < 4 or len(coords) % 2:
            continue
        points = [
            (
                round(VIEW_WIDTH / 2 + coords[i] * scale),
                round(VIEW_HEIGHT / 2 + coords[i + 1] * scale),
            )
            for i in range(0, len(coords), 2)
        ]
        points = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
        points = simplify_screen(points)
        if len(points) < 2 or not any(p != points[0] for p in points[1:]):
            continue
        tokens = [f"M{points[0][0]} {points[0][1]}"]
        tokens.extend(f"{x} {y}" for x, y in points[1:])
        groups[name].append(" ".join(tokens))
    return {kind: " ".join(paths) for kind, paths in groups.items() if paths}


def wrapped_svg_path(path):
    """Keep generated HTML lines below the project's 100-character limit."""
    lines, current = [], "        "
    for word in path.split():
        if len(current) + len(word) + 1 > 92:
            lines.append(current.rstrip())
            current = "        " + word
        else:
            current += (" " if current.strip() else "") + word
    if current.strip():
        lines.append(current.rstrip())
    return "\n".join(lines)


def render_svg(road_json):
    road_json = Path(road_json)
    groups = (
        grouped_paths(json.loads(road_json.read_text(encoding="ascii")))
        if road_json.is_file()
        else {}
    )
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg"',
        f'    viewBox="0 0 {VIEW_WIDTH} {VIEW_HEIGHT}"',
        f'    width="{VIEW_WIDTH}" height="{VIEW_HEIGHT}"',
        '    preserveAspectRatio="xMidYMid meet">',
        "    <title>Moscow road network</title>",
        "    <desc>Road data: OpenStreetMap contributors</desc>",
        '    <g fill="none" stroke-linecap="round" stroke-linejoin="round">',
    ]
    for kind, geometry in groups.items():
        color, width = STYLES[kind]
        lines.extend(
            [
                f'    <path data-road-class="{kind}" d="',
                wrapped_svg_path(geometry),
                f'    " stroke="{color}" stroke-width="{width}"/>',
            ]
        )
    lines.extend(["    </g>", "</svg>"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("build/moscow-major-roads.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("site/moscow-major-roads.svg")
    )
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"Road geometry missing: {args.input}")
    from .site_builder import write_atomic_bytes

    write_atomic_bytes(args.output, render_svg(args.input).encode("ascii"))
    print(f"[boot] wrote: {args.output}")


if __name__ == "__main__":
    main()
