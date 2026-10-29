#!/usr/bin/env python3
"""Pure geometry tests; a full OSM PBF integration run needs pyosmium."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import major_roads as roads


class MajorRoadsTests(unittest.TestCase):
    def test_connects_exact_shared_node(self):
        ways = [("trunk", [1, 2]), ("trunk", [2, 3])]
        actual = list(roads.merge_chains(ways))
        self.assertEqual(actual, [("trunk", [1, 2, 3])])

    def test_does_not_join_different_osm_nodes_even_when_close(self):
        ways = [("trunk", [1, 2]), ("trunk", [3, 4])]
        positions = {1: (0, 0), 2: (5, 0), 3: (5.01, 0), 4: (10, 0)}
        output = roads.generate_from_ways(ways, positions)
        self.assertEqual(len(output["paths"]), 2)

    def test_preserves_junctions(self):
        ways = [("trunk", [1, 2]), ("trunk", [2, 3]), ("trunk", [2, 4])]
        self.assertEqual(len(list(roads.merge_chains(ways))), 3)

    def test_continues_straight_across_intersection(self):
        ways = [("trunk", [1, 2]), ("trunk", [2, 3]), ("trunk", [2, 4])]
        positions = {1: (0, 0), 2: (4, 0), 3: (8, 0), 4: (4, 5)}
        joined = list(roads.merge_chains(ways, positions))
        self.assertEqual(len(joined), 2)
        self.assertIn(("trunk", [1, 2, 3]), joined)

    def test_clip_keeps_border_and_never_bridges_outside(self):
        mx = roads.MAX_X
        coords = [(-mx - 10, 0), (-mx + 10, 0), (-mx + 12, 0)]
        visible = list(roads.clipped_paths(coords))
        self.assertEqual(len(visible), 1)
        self.assertAlmostEqual(visible[0][0][0], -mx)
        self.assertEqual(visible[0][-1], (-mx + 12, 0))

    def test_ring_names(self):
        self.assertEqual(
            roads.road_class(
                {
                    "highway": "secondary",
                    "name": (
                        "\u0421\u0430\u0434\u043e\u0432\u043e\u0435 "
                        "\u043a\u043e\u043b\u044c\u0446\u043e"
                    ),
                }
            ),
            "ring",
        )
        self.assertEqual(
            roads.road_class(
                {
                    "highway": "motorway",
                    "name": "\u041c\u041a\u0410\u0414",
                }
            ),
            "mkad",
        )
        self.assertIsNone(roads.road_class({"highway": "residential"}))
        self.assertIsNone(roads.road_class({"highway": "primary_link"}))

    def test_no_fake_bridge_across_missing_node(self):
        ways = [("primary", [1, 2, 3])]
        positions = {1: (0, 0), 3: (10, 0)}
        self.assertEqual(roads.generate_from_ways(ways, positions)["paths"], [])

    def test_metric_projection_is_not_stretched_by_viewport(self):
        cx, cy = roads.project(roads.CENTER_LON, roads.CENTER_LAT)
        self.assertAlmostEqual(cx, 0)
        self.assertAlmostEqual(cy, 0)
        east, _ = roads.project(roads.CENTER_LON + 0.01, roads.CENTER_LAT)
        _, north = roads.project(roads.CENTER_LON, roads.CENTER_LAT + 0.01)
        self.assertGreater(abs(north), abs(east))


if __name__ == "__main__":
    unittest.main()
