"""Validate translation catalogs and render the default first-paint HTML."""

import html
import json
import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "web" / "i18n"
TOKEN = re.compile(r"@@I18N:(\w+)@@")
PARAM = re.compile(r"\{(\w+)\}")


def load_catalogs(source=SOURCE):
    catalogs = {}
    for path in sorted(source.glob("*.json")):
        if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]+)*", path.stem):
            raise ValueError(f"Invalid locale filename: {path.name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            raise ValueError(f"Missing language name: {path.name}")
        messages = data.get("messages")
        if not isinstance(messages, dict) or not all(
            isinstance(v, str) and v for v in messages.values()
        ):
            raise ValueError(f"Invalid messages: {path.name}")
        catalogs[path.stem] = data
    if "ru" not in catalogs:
        raise ValueError("Missing default Russian catalog")
    base = catalogs["en"]["messages"]
    for code, catalog in catalogs.items():
        messages = catalog["messages"]
        if messages.keys() != base.keys():
            missing = base.keys() - messages.keys()
            extra = messages.keys() - base.keys()
            raise ValueError(f"{code}: missing {missing}, extra {extra}")
        for key, value in messages.items():
            if sorted(PARAM.findall(value)) != sorted(PARAM.findall(base[key])):
                raise ValueError(f"{code}: mismatched parameters for {key}")
    return catalogs


def wrap_html_lines(page, width=100):
    """Wrap template whitespace, never split quoted HTML attributes or words."""
    result = []
    in_tag = False
    quote = None
    for line in page.splitlines():
        starts_in_quote = quote is not None
        breaks = []
        for pos, char in enumerate(line):
            if quote:
                if char == quote:
                    quote = None
            elif in_tag:
                if char in ('"', "'"):
                    quote = char
                elif char == ">":
                    in_tag = False
                elif char in " \t":
                    breaks.append(pos)
            elif char == "<":
                in_tag = True
            elif char in " \t":
                breaks.append(pos)

        # A newline is equivalent to whitespace in this HTML template. Do not
        # introduce whitespace where none was present, or reformat raw JS/CSS.
        if starts_in_quote or len(line.rstrip()) <= width:
            result.append(line)
            continue
        indent = " " * min(len(line) - len(line.lstrip()), 12)
        offset = len(line) - len(line.lstrip())
        while len(indent) + len(line[offset:].rstrip()) > width:
            limit = offset + width - len(indent)
            candidates = [pos for pos in breaks if offset < pos <= limit]
            if not candidates:
                break
            cut = candidates[-1]
            result.append(indent + line[offset:cut].rstrip())
            offset = cut + 1
            while offset < len(line) and line[offset] in " \t":
                offset += 1
        result.append(indent + line[offset:])
    return "\n".join(result) + "\n"


def render_default(page, catalogs, walk_speed):
    messages = catalogs["ru"]["messages"]

    def replace(match):
        value = messages[match[1]].replace("{speed}", f"{walk_speed:.0f}")
        return html.escape(value)

    return TOKEN.sub(replace, page)


def publish_catalogs(directory, catalogs, write):
    directory = Path(directory)
    for code, data in catalogs.items():
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
        write(directory / f"{code}.json", payload.encode())
    manifest = [{"code": code, "name": data["name"]} for code, data in catalogs.items()]
    write(
        directory / "index.json",
        (json.dumps(manifest, ensure_ascii=False) + "\n").encode(),
    )
