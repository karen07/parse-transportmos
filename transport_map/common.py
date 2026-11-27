"""Common for the Moscow transport map."""

import hashlib
import math
import re
from pathlib import Path

try:
    import osmium
except ImportError:
    osmium = None


OsmiumSimpleHandler = osmium.SimpleHandler if osmium is not None else object


PROJECT_VERSION = "0.17.3"


def cache_implementation():
    """Fingerprint the code that defines generated graph caches."""
    digest = hashlib.sha256()
    root = Path(__file__).parent
    for name in (
        "common.py",
        "walking.py",
        "pedestrian.py",
        "station_access.py",
        "station_osm.py",
        "transfers.py",
        "transport_index.py",
        "transport.py",
    ):
        digest.update(name.encode("ascii"))
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


# OSM extraction and normalization
EARTH_RADIUS_M = 6371000.0


# OSMlab provides separate extracts for Moscow city and Moscow Oblast.
# Restore original names to reuse the OSMlab cache without mixing OSM France data.
SOURCE_REGIONS = (
    ("RU-MOW", "https://www.osmlab.ru/regions/RU-MOW.pbf"),
    ("RU-MOS", "https://www.osmlab.ru/regions/RU-MOS.pbf"),
)


MKAD_GEOJSON_URL = (
    "https://raw.githubusercontent.com/karen07/moscow-geo/main/geojson/mkad.geojson"
)


MKAD_GEOJSON_CACHE_NAME = "mkad.geojson"


MERGED_SOURCE_NAME = "moscow-regions-osmlab.osm.pbf"


MERGED_MANIFEST_NAME = "moscow-regions-osmlab.sources.json"


TRANSPORT_CACHE_NAME = "moscow-transport-osmlab.osm.pbf"


PEDESTRIAN_CACHE_NAME = "moscow-pedestrian-osmlab.json.gz"


PEDESTRIAN_TEST_CACHE_NAME = "moscow-pedestrian-osmlab-private-test.json.gz"


TRANSFERS_CACHE_NAME = "moscow-walk-transfers-osmlab.json.gz"


TRANSFERS_TEST_CACHE_NAME = "moscow-walk-transfers-osmlab-private-test.json.gz"


SURFACE_MODES = {"bus", "tram", "trolleybus"}


RAIL_ROUTE_TYPES = {"subway", "train", "light_rail", "railway"}


# Explicit intercity services are not ordinary suburban trains.
INTERCITY_SERVICES = {"long_distance", "high_speed", "intercity", "international"}


# Keep commuter stops inside the Moscow catchment in the final index.
SUPPORTED_ROUTE_TYPES = SURFACE_MODES | RAIL_ROUTE_TYPES


MCD_REF_PATTERN = re.compile(
    r"(?<![A-Za-z\u0410-\u042f\u0430-\u044f0-9])"
    r"[D\u0414][1-4]"
    r"(?![A-Za-z\u0410-\u042f\u0430-\u044f0-9])",
    re.IGNORECASE,
)


MCC_SHORT = "\u043c\u0446\u043a"


MCC_FULL = (
    "\u043c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u043e\u0435 "
    "\u0446\u0435\u043d\u0442\u0440\u0430\u043b\u044c\u043d\u043e"
    "\u0435 \u043a\u043e\u043b\u044c\u0446\u043e"
)


MCD_SHORT = "\u043c\u0446\u0434"


MCD_FULL = (
    "\u043c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u0438\u0435 "
    "\u0446\u0435\u043d\u0442\u0440\u0430\u043b\u044c\u043d\u044b"
    "\u0435 \u0434\u0438\u0430\u043c\u0435\u0442\u0440\u044b"
)


METROPOLITAN = (
    "\u043c\u0435\u0442\u0440\u043e\u043f\u043e\u043b\u0438\u0442" "\u0435\u043d"
)


MOSCOW_METROPOLITAN = (
    "\u043c\u043e\u0441\u043a\u043e\u0432\u0441\u043a\u0438\u0439 "
    "\u043c\u0435\u0442\u0440\u043e\u043f\u043e\u043b\u0438\u0442"
    "\u0435\u043d"
)


def member_key(member_type, member_id):
    return f"{member_type}{member_id}"


def coord_distance_m(a, b):
    mean_lat = math.radians((a[1] + b[1]) / 2.0)
    dx = math.radians(a[0] - b[0]) * math.cos(mean_lat)
    dy = math.radians(a[1] - b[1])
    return EARTH_RADIUS_M * math.hypot(dx, dy)


def stop_fingerprint(stops):
    digest = hashlib.sha256()
    for stop in stops:
        digest.update(
            (
                f"{stop['id']}:{stop['lat']:.6f}:{stop['lon']:.6f}:"
                f"{','.join(stop.get('modes', ()))};"
            ).encode("ascii")
        )
    return digest.hexdigest()


# Transport graph and spatial index
def project(lon, lat, origin_lon, origin_lat):
    origin_lat_rad = math.radians(origin_lat)

    x = EARTH_RADIUS_M * math.radians(lon - origin_lon) * math.cos(origin_lat_rad)

    y = EARTH_RADIUS_M * math.radians(lat - origin_lat)

    return x, y


WALK_DENIED = {
    "no",
    "private",
    "agricultural",
    "forestry",
    "delivery",
    "use_sidepath",
}
