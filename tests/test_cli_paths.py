"""Regression tests for predictable default build and publication paths."""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "build-transport-map.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("transport_builder", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CliPathsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()

    def test_defaults(self):
        with patch.object(sys, "argv", ["build-transport-map.py"]):
            args = self.builder.parse_args()
        self.assertEqual(args.output, "dist/routes.html")
        self.assertEqual(args.cache_dir, "build")

    def test_paths_can_be_overridden(self):
        argv = [
            "build-transport-map.py",
            "-o",
            "custom/page.html",
            "--cache-dir",
            "custom-cache",
        ]
        with patch.object(sys, "argv", argv):
            args = self.builder.parse_args()
        self.assertEqual(args.output, "custom/page.html")
        self.assertEqual(args.cache_dir, "custom-cache")


if __name__ == "__main__":
    unittest.main()
