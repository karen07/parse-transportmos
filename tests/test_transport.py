"""Deterministic tests requiring neither a Moscow PBF nor internet access."""

import importlib
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transportmap.audit import audit_index
from transportmap.index import build_index
from transportmap.renderer import build_html


class IndexAndRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (ROOT / "tests" / "fixture-transport.geojson").read_text()
        )

    def test_audit_provenance(self):
        report = audit_index(build_index(self.fixture, 400))
        self.assertEqual(report["mode_directions"], {"bus": 1, "mcc": 1})
        self.assertEqual(report["synthetic_route_relations"], [])
        self.assertEqual(report["geometry_sources"], {"osm_ways": 1, "stop_chain": 1})

    def test_real_relation_routes_only(self):
        data = build_index(self.fixture, 400)
        self.assertEqual(set(data["routes"]), {"mcc:test", "bus:7"})
        self.assertEqual(data["routes"]["mcc:test"]["mode"], "mcc")
        self.assertEqual(data["routes"]["mcc:test"]["directions"][0]["stops"], [0, 1, 2])
        self.assertEqual(data["routes"]["bus:7"]["directions"][0]["stops"], [2, 3])

    def test_single_file_ascii_and_mode_defaults(self):
        html = build_html(build_index(self.fixture, 400), 5, 80, 40)
        self.assertTrue(html.isascii())
        self.assertLessEqual(max(map(len, html.splitlines())), 100)
        self.assertIn('content="0.3.0"', html)
        self.assertIn("https://tiles.openfreemap.org/styles/bright", html)
        self.assertNotIn("mcc:osm-station-ring-fallback", html)
        for mode in ("bus", "tram", "trolleybus"):
            self.assertRegex(
                html,
                rf'value="{mode}"\s+checked',
            )
        for mode in ("metro", "mcc", "mcd"):
            self.assertIn(f'value="{mode}">', html)
        self.assertIn("function isRouteEnabled", html)
        self.assertIn("routeSegmentGeometry(segment)", html)
        self.assertIn("function legDistanceMeters", html)
        self.assertIn("stopChainDistanceMeters(leg) * factor", html)

    def test_javascript_syntax(self):
        html = build_html(build_index(self.fixture, 400), 5, 80, 40)
        js = html.split("<script>", 1)[1].split("</script>", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "generated.js"
            script.write_text(js, encoding="ascii")
            checked = subprocess.run(
                ["node", "--check", str(script)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_no_direction_from_single_stop(self):
        feature = dict(self.fixture["features"][0])
        feature["properties"] = dict(feature["properties"])
        feature["properties"]["stops"] = ["n1"]
        collection = {"features": [feature] + self.fixture["features"][2:]}
        self.assertEqual(build_index(collection, 400)["routes"], {})


class OSMExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # pyosmium is intentionally optional when building from a fixture.
        cls.stub = types.ModuleType("osmium")
        cls.stub.SimpleHandler = type("SimpleHandler", (), {})
        with patch.dict(sys.modules, {"osmium": cls.stub}):
            cls.osm = importlib.import_module("transportmap.osm_data")

    def test_mcc_mode_is_inherited_without_synthetic_ring(self):
        osm = self.osm

        class FakeRelations:
            def __init__(self):
                self.masters = [
                    {
                        "route": "train", "mode": "mcc", "ref": "14",
                        "relation_id": 500, "members": [100],
                    }
                ]
                self.routes = [
                    {
                        "relation_id": 100, "mode": "metro", "route": "subway",
                        "ref": "14", "roundtrip": "no", "from": "A", "to": "C",
                        "stop_members": ["n1", "n2", "n3"],
                        "stop_roles": ["stop"] * 3, "way_members": [],
                    }
                ]

            def apply_file(self, _path):
                return None

        class FakeObjects:
            def __init__(self, *_args):
                self.objects = {
                    f"n{i}": {"lon": 37.5 + i * 0.01,
                              "lat": 55.7, "name": f"MCC {i}", "ref": None}
                    for i in range(1, 4)
                }
                self.stations = dict(self.objects)
                self.route_ways = {}

            def apply_file(self, _path, locations=False):
                return None

        with patch.object(osm, "RelationHandler", FakeRelations), patch.object(
            osm, "ObjectHandler", FakeObjects
        ):
            features = osm.build_transport_data("fake.pbf")["features"]
        routes = [item for item in features if item["properties"]["feature_type"] == "route"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["properties"]["mode"], "mcc")
        self.assertEqual(routes[0]["properties"]["relation_id"], 100)
        self.assertEqual(routes[0]["properties"]["geometry_source"], "stop_chain")
        self.assertEqual(len(routes[0]["properties"]["stops"]), 3)
        self.assertTrue(all(item["properties"]["relation_id"] > 0 for item in routes))

    def test_no_route_created_from_station_names(self):
        osm = self.osm

        class NoRelations:
            def __init__(self):
                self.masters = []
                self.routes = []

            def apply_file(self, _path):
                return None

        class StationOnly:
            def __init__(self, *_args):
                self.objects = {}
                self.route_ways = {}
                self.stations = {"n1": {"lon": 37.5, "lat": 55.7,
                                        "name": "Some MCC station"}}

            def apply_file(self, _path, locations=False):
                return None

        with patch.object(osm, "RelationHandler", NoRelations), patch.object(
            osm, "ObjectHandler", StationOnly
        ):
            collection = osm.build_transport_data("fake.pbf")
        self.assertEqual(collection["features"], [])


if __name__ == "__main__":
    unittest.main()
