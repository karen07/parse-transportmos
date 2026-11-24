"""Station osm for the Moscow transport map."""

import math
import re
from collections import defaultdict

from .common import WALK_DENIED, OsmiumSimpleHandler, project


# A route stop is a boarding position, not necessarily a street entrance.
# Only same-stop_area membership authorizes a virtual indoor connection.
# These links are routing estimates, not mapped pedestrian geometries.
def canonical_station_name(name):
    """Conservative normalization, never fuzzy-match station names."""
    name = (name or "").casefold().replace("\u0451", "\u0435").strip()
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    station = "\u0441\u0442\u0430\u043d\u0446\u0438\u044f"
    metro = "\u043c\u0435\u0442\u0440\u043e"
    platform = "\u043f\u043b\u0430\u0442\u0444\u043e\u0440\u043c\u0430"
    stopping_point = (
        "\u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043e\u0447\u043d\u044b\u0439 "
        "\u043f\u0443\u043d\u043a\u0442"
    )
    name = re.sub(rf"^(?:{station}\s+)?(?:{metro}\s+)", "", name)
    name = re.sub(rf"^(?:{station}|{platform}|{stopping_point})\s+", "", name)
    return " ".join(name.split())


class StationEntranceCollector(OsmiumSimpleHandler):
    """Retain complete stop_area membership, including non-route station IDs.

    A route commonly refers to stop_position on the rail, while stop_area
    contains a separately mapped railway=station or platform object. The
    original exact-ID-only matching silently dropped such stations.
    """

    RAIL_MODES = frozenset(("metro", "mcc", "mcd", "train"))

    def __init__(self, stops, origin):
        super().__init__()
        self.origin = origin
        self.rail_stops = {
            stop["id"] for stop in stops if set(stop.get("modes", ())) & self.RAIL_MODES
        }
        self.entrances = {}
        self.station_names = {}
        self.station_centers = {}
        self.platform_objects = set()
        self.platform_point_levels = {}
        self._way_platform_names = {}
        self.facility_nodes = {}
        self.facility_access = defaultdict(dict)
        self.areas = []
        self.area_groups = []
        self.area_route_stops = defaultdict(set)
        self.access = defaultdict(dict)
        self.platform_access = defaultdict(dict)
        self.matched_areas = 0
        self.resolved_exact = 0
        self.resolved_named = 0

    def node(self, node):
        if not node.location.valid():
            return
        tags = node.tags
        if tags.get("railway") in ("station", "halt", "platform") or (
            tags.get("public_transport") in ("station", "platform")
        ):
            key = "n" + str(node.id)
            if tags.get("railway") == "platform" or (
                tags.get("public_transport") == "platform"
            ):
                self.platform_objects.add(key)
                self.platform_point_levels[key] = (tags.get("level"), tags.get("layer"))
            x, y = project(node.location.lon, node.location.lat, *self.origin)
            self.station_centers[key] = (round(x, 1), round(y, 1))
            if tags.get("name"):
                self.station_names[key] = tags.get("name")
            if tags.get("railway") in ("station", "halt"):
                self.facility_nodes[node.id] = {
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "name": tags.get("name"),
                    "layer": tags.get("layer"),
                    "level": tags.get("level"),
                }
        is_entrance = (
            tags.get("railway") in ("subway_entrance", "train_station_entrance")
            or tags.get("entrance") in ("yes", "main", "entrance", "exit")
            or tags.get("public_transport") == "entrance"
            or tags.get("highway") == "elevator"
        )
        if not is_entrance:
            return
        if (tags.get("access") or "").lower() in WALK_DENIED:
            return
        if (tags.get("foot") or "").lower() in WALK_DENIED:
            return
        x, y = project(node.location.lon, node.location.lat, *self.origin)
        role = (tags.get("entrance") or "").lower()
        direction = 12 if role == "exit" else 13 if role == "entrance" else 11
        self.entrances[node.id] = (round(x, 1), round(y, 1), direction)

    def relation(self, relation):
        kind = relation.tags.get("public_transport")
        if kind not in ("stop_area", "stop_area_group"):
            return
        members = [
            (member.type + str(member.ref), member.role or "")
            for member in relation.members
        ]
        if kind == "stop_area_group":
            self.area_groups.append((relation.id, members))
        else:
            self.areas.append((relation.id, relation.tags.get("name"), members))

    def resolve(self, stops, way_names=None, way_centers=None, platform_way_ids=None):
        """Match same-area stops by ID, or by station name AND proximity.

        Only a real stop_area relation grants access. Name-only matching
        never links entrances that are not already members of the relation.
        """
        way_names = way_names or {}
        way_centers = way_centers or {}
        platform_keys = self.platform_objects | {
            "w" + str(ident) for ident in (platform_way_ids or ())
        }
        stop_by_id = {
            stop["id"]: stop for stop in stops if stop["id"] in self.rail_stops
        }
        by_name = defaultdict(list)
        for stop in stop_by_id.values():
            label = canonical_station_name(stop.get("name"))
            if label:
                by_name[label].append(stop)
        areas_seen = 0
        for relation_id, area_name, members in self.areas:
            entrances = {}
            for key, role in members:
                if not key.startswith("n"):
                    continue
                entry = self.entrances.get(int(key[1:]))
                if entry is None:
                    continue
                direction = entry[2]
                if role == "entry_only":
                    direction = 13
                elif role == "exit_only":
                    direction = 12
                entrances[int(key[1:])] = (entry[0], entry[1], direction)
            # A stop_area can identify a transfer even if no station
            # entrances are mapped. Do not discard its membership.
            locations = [entry[:2] for entry in entrances.values()]
            for key, _role in members:
                center = self.station_centers.get(key) or way_centers.get(key)
                if center is not None:
                    locations.append(center)
            labels = set()
            name = canonical_station_name(area_name)
            if name:
                labels.add(name)
            for key, _role in members:
                member_name = self.station_names.get(key) or way_names.get(key)
                if member_name:
                    labels.add(canonical_station_name(member_name))
            exact = {key for key, _role in members if key in stop_by_id}
            matched = set(exact)
            for label in labels:
                for stop in by_name.get(label, ()):
                    if stop["id"] in matched:
                        continue
                    # A stop_area is station-specific, but names can repeat.
                    # Match named platform/station geometry or an entrance,
                    # never an arbitrary equal name across the city.
                    limit = 400.0 if "metro" in stop.get("modes", ()) else 650.0
                    near = any(
                        math.hypot(stop["x"] - x, stop["y"] - y) <= limit
                        for x, y in locations
                    )
                    if near:
                        matched.add(stop["id"])
            if not matched:
                continue
            areas_seen += 1
            self.area_route_stops[relation_id].update(matched)
            self.resolved_exact += len(exact)
            self.resolved_named += len(matched - exact)
            # PTv2 often maps train platforms but no named building entrance.
            # These are candidate boarding nodes, NOT inferred street links.
            platforms = [
                (key, role)
                for key, role in members
                if key[:1] in ("n", "w")
                and (role.startswith("platform") or key in platform_keys)
            ]
            for stop_id in matched:
                stop = stop_by_id[stop_id]
                limit = 400.0 if "metro" in stop.get("modes", ()) else 650.0
                for key, role in platforms:
                    center = self.station_centers.get(key) or way_centers.get(key)
                    if center is None:
                        continue
                    distance = math.hypot(stop["x"] - center[0], stop["y"] - center[1])
                    if distance > limit:
                        continue
                    self.platform_access[stop_id][key] = role
                for entrance_id, entrance in entrances.items():
                    # Avoid an exit for a different platform at far end of
                    # an interchange when the stop was name-matched.
                    if stop_id not in exact:
                        stop = stop_by_id[stop_id]
                        limit = 400.0 if "metro" in stop.get("modes", ()) else 650.0
                        if (
                            math.hypot(stop["x"] - entrance[0], stop["y"] - entrance[1])
                            > limit
                        ):
                            continue
                    self.access[stop_id][entrance_id] = entrance
        self.matched_areas = areas_seen
        print(
            "[station] stop_area matches:",
            areas_seen,
            "exact:",
            self.resolved_exact,
            "name+distance:",
            self.resolved_named,
        )
