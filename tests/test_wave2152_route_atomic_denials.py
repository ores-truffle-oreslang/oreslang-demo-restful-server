"""Host route publication must preserve the last good artifacts on invalid URLs.

These tests exercise the actual generator, not AttoLang guest/compiler execution.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "generate-routes.py"
spec = importlib.util.spec_from_file_location("wave2152_routes", SOURCE)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class AtomicDenialAcrossRouteNames(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="wave2152-routes-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.rest = self.root / "src" / "routes" / "rest"
        (self.rest / "health").mkdir(parents=True)
        (self.rest / "health" / "get.ores").write_text("healthy route", encoding="utf-8")
        root_patch = patch.object(generator, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.data = self.root / "data"
        generator.generate(3000, self.data)
        generated = self.root / "src" / "generated"
        self.output_paths = [generated / name for name in ("routes.ores", "config.ores")]
        self.before = [path.read_bytes() for path in self.output_paths]

    def assert_unchanged(self):
        self.assertEqual([path.read_bytes() for path in self.output_paths], self.before)
        self.assertEqual(list((self.root / "src" / "generated").glob(".routes-*")), [])

    def test_percent_encoded_and_unicode_segments_never_publish_partial_source(self):
        for segment in ("encoded%2Fadmin", "name space", "colon:header", "unicode🙂"):
            with self.subTest(segment=segment):
                candidate = self.rest / segment
                candidate.mkdir()
                (candidate / "post.ores").write_text("untrusted route", encoding="utf-8")
                try:
                    with self.assertRaisesRegex(ValueError, "literal URL segments"):
                        generator.generate(3000, self.data)
                    self.assert_unchanged()
                finally:
                    (candidate / "post.ores").unlink()
                    candidate.rmdir()
                generator.generate(3000, self.data)
                self.assert_unchanged()

    def test_unsupported_near_method_does_not_replace_a_good_route_table(self):
        for method in ("GET.ores", "trace.ores", "post.old.ores"):
            with self.subTest(method=method):
                candidate = self.rest / "health" / method
                candidate.write_text("unexpected method", encoding="utf-8")
                try:
                    with self.assertRaisesRegex(ValueError, "unsupported method filename"):
                        generator.generate(3000, self.data)
                    self.assert_unchanged()
                finally:
                    candidate.unlink()
        safe = self.rest / "team_3-alpha"
        safe.mkdir()
        (safe / "post.ores").write_text("positive control", encoding="utf-8")
        generator.generate(3000, self.data)
        route_source = self.output_paths[0].read_text(encoding="utf-8")
        self.assertIn("GET /health", route_source)
        self.assertIn("POST /team_3-alpha", route_source)


if __name__ == "__main__":
    unittest.main()
