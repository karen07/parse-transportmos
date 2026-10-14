"""Embed static HTML/CSS/JS and compressed transport index in one HTML file."""

import base64
import gzip
import html as html_lib
import json
import re
from pathlib import Path

from . import PROJECT_VERSION

ASSETS = Path(__file__).resolve().parent / "assets"

def build_html(
    data,
    initial_walk_minutes,
    walk_speed,
    initial_circle_opacity,
):
    raw_payload = json.dumps(
        data,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")

    compressed_payload = gzip.compress(
        raw_payload,
        compresslevel=9,
        mtime=0,
    )

    payload = base64.b64encode(compressed_payload).decode("ascii")
    payload_lines = ",\n".join(
        f'    "{payload[offset : offset + 72]}"'
        for offset in range(0, len(payload), 72)
    )

    print(
        "embedded data:",
        f"{len(raw_payload) / (1024 * 1024):.1f} MiB raw ->",
        f"{len(compressed_payload) / (1024 * 1024):.1f} MiB gzip ->",
        f"{len(payload) / (1024 * 1024):.1f} MiB base64",
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

    # Keep localized labels ASCII-encoded in the source and generated HTML.
    mode_names = {
        "heading": (
            "&#1042;&#1080;&#1076;&#1099; &#1090;&#1088;&#1072;&#1085;&#1089;"
            "&#1087;&#1086;&#1088;&#1090;&#1072;"
        ),
        "bus": "&#1040;&#1074;&#1090;&#1086;&#1073;&#1091;&#1089;",
        "tram": "&#1058;&#1088;&#1072;&#1084;&#1074;&#1072;&#1081;",
        "trolleybus": (
            "&#1058;&#1088;&#1086;&#1083;&#1083;&#1077;&#1081;&#1073;&#1091;"
            "&#1089;"
        ),
        "metro": "&#1052;&#1077;&#1090;&#1088;&#1086;",
        "mcc": "&#1052;&#1062;&#1050;",
        "mcd": "&#1052;&#1062;&#1044;",
    }
    mode_hint = (
        "&#1055;&#1086; &#1091;&#1084;&#1086;&#1083;&#1095;&#1072;&#1085;"
        "&#1080;&#1102; &#1074;&#1082;&#1083;&#1102;&#1095;&#1105;&#1085; "
        "&#1085;&#1072;&#1079;&#1077;&#1084;&#1085;&#1099;&#1081; &#1090;"
        "&#1088;&#1072;&#1085;&#1089;&#1087;&#1086;&#1088;&#1090;. &#1057;"
        "&#1082;&#1086;&#1088;&#1086;&#1089;&#1090;&#1080; &#1080; &#1074;"
        "&#1088;&#1077;&#1084;&#1103; &#1086;&#1078;&#1080;&#1076;&#1072;"
        "&#1085;&#1080;&#1103; &#1087;&#1088;&#1080;&#1073;&#1083;&#1080;"
        "&#1079;&#1080;&#1090;&#1077;&#1083;&#1100;&#1085;&#1099;&#1077;."
    )

    # Keep generated HTML ASCII-safe even after tidy rewrites HTML entities.
    def js_escaped_string(value, width=64):
        escaped = json.dumps(value, ensure_ascii=True)[1:-1]
        tokens = re.findall(r"\\u[0-9A-Fa-f]{4}|\\.|.", escaped)
        chunks = []
        current = ""

        for token in tokens:
            if len(current) + len(token) > width and current:
                chunks.append(current)
                current = ""

            current += token

        if current:
            chunks.append(current)

        return " +\n    ".join('"' + chunk + '"' for chunk in chunks)

    js_mode_names = {
        key: js_escaped_string(html_lib.unescape(value))
        for key, value in mode_names.items()
    }
    js_mode_hint = js_escaped_string(html_lib.unescape(mode_hint))

    script = (ASSETS / "map.js").read_text(encoding="ascii")
    script += "\n" + (ASSETS / "routing.js").read_text(encoding="ascii")
    css = (ASSETS / "map.css").read_text(encoding="ascii")
    html = (ASSETS / "page.html").read_text(encoding="ascii")

    replacements = {
        "@@VERSION@@": PROJECT_VERSION,
        "@@INITIAL_WALK_MINUTES@@": f"{initial_walk_minutes:.0f}",
        "@@INITIAL_OPACITY@@": f"{initial_circle_opacity:.0f}",
        "@@WALK_SPEED@@": f"{walk_speed:.0f}",
        "@@PAYLOAD_LINES@@": payload_lines,
        "@@GRID_SIZE@@": f"{grid_size:.6f}",
        "@@ORIGIN_LON@@": f"{origin_lon:.6f}",
        "@@ORIGIN_LAT@@": f"{origin_lat:.6f}",
        "@@MODE_HEADING@@": js_mode_names["heading"],
        "@@MODE_BUS@@": js_mode_names["bus"],
        "@@MODE_TRAM@@": js_mode_names["tram"],
        "@@MODE_TROLLEYBUS@@": js_mode_names["trolleybus"],
        "@@MODE_METRO@@": js_mode_names["metro"],
        "@@MODE_MCC@@": js_mode_names["mcc"],
        "@@MODE_MCD@@": js_mode_names["mcd"],
        "@@MODE_HINT@@": js_mode_hint,
        "@@WALK_SPEED_JS@@": f"{walk_speed:.6f}",
    }
    # Same placeholder can occur in the HTML note and in JavaScript,
    # requiring different numerical precision in the latter.
    script = script.replace("@@WALK_SPEED@@", "@@WALK_SPEED_JS@@")
    html = html.replace("@@CSS@@", css.strip())
    html = html.replace("@@JS@@", script.strip())
    for key, value in replacements.items():
        html = html.replace(key, value)

    if re.search(r"@@[A-Z_]+@@", html):
        raise ValueError("Unresolved HTML template placeholder")
    if not html.isascii():
        raise ValueError("Generated HTML must contain ASCII only")
    result = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
    long_lines = [i for i, line in enumerate(result.splitlines(), 1) if len(line) > 100]
    if long_lines:
        raise ValueError(f"Generated HTML lines exceed 100: {long_lines[:10]}")
    return result
