"""First-paint road asset is bounded, deterministic and topology-preserving."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boot_svg import MAX_PATH_CHARS, render_inline_svg, svg_path, wrapped_svg_path


def fixture():
    return {
        "format": "moscow-major-roads-v1",
        "extent": [850, 637.5],
        "paths": [
            [0, [-10, 0, 0, 0, 10, 0]],
            [3, [0, -20, 0, -10, 0, 0]],
            [3, [0, 0, 10, 0, 20, 0]],
        ],
    }


class BootSvgTest(unittest.TestCase):
    def test_single_path_contains_all_connected_road_segments(self):
        path = svg_path(fixture())
        self.assertEqual(path.count("M"), 3)
        self.assertNotIn("NaN", path)

    def test_byte_cap_skips_whole_paths_without_creating_fake_bridges(self):
        path = svg_path(fixture(), max_chars=22)
        self.assertLessEqual(len(path), 22)
        self.assertEqual(path.count("M"), 1)

    def test_wrapping_keeps_all_coordinates(self):
        path = svg_path(fixture())
        wrapped = wrapped_svg_path(path)
        self.assertEqual(path.split(), wrapped.split())
        self.assertTrue(all(len(line) <= 92 for line in wrapped.splitlines()))

    def test_default_budget_keeps_initial_html_small(self):
        path = svg_path(fixture())
        self.assertLessEqual(len(path), MAX_PATH_CHARS)

    def test_missing_asset_renders_empty_static_layer(self):
        svg = render_inline_svg("/missing/moscow-major-roads.json")
        self.assertIn('id="boot-road-path"', svg)
        self.assertNotIn("M0", svg)


if __name__ == "__main__":
    unittest.main()
