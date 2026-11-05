"""Host-only route publisher repeatability and lexical-boundary tests."""
import unittest
from test_route_publication import RoutePublication, generator


class RoutePublicationBoundaries(unittest.TestCase):
    setUp = RoutePublication.setUp

    def test_repeat_publishing_with_same_inputs_is_byte_identical(self):
        generator.generate(3000, self.root / "data")
        directory = self.root / "src/generated"
        first = {name: (directory / name).read_bytes()
                 for name in ("routes.ores", "config.ores")}
        generator.generate(3000, self.root / "data")
        for name, expected in first.items():
            with self.subTest(name=name):
                self.assertEqual((directory / name).read_bytes(), expected)

    def test_multilevel_literal_segments_are_not_misinterpreted(self):
        nested = self.root / "src/routes/rest/api/v1_2"
        nested.mkdir(parents=True)
        (nested / "delete.ores").write_text("fixture")
        generator.generate(3000, self.root / "data")
        routes = (self.root / "src/generated/routes.ores").read_text()
        self.assertIn("DELETE /api/v1_2", routes)
        self.assertIn('http_routing.literal("api")', routes)
        self.assertIn('http_routing.literal("v1_2")', routes)

    def test_unicode_segment_rejection_preserves_both_previous_files(self):
        generator.generate(3000, self.root / "data")
        generated = self.root / "src/generated"
        before = {name: (generated / name).read_bytes()
                  for name in ("routes.ores", "config.ores")}
        invalid = self.root / "src/routes/rest/café"
        invalid.mkdir()
        (invalid / "get.ores").write_text("fixture")
        with self.assertRaisesRegex(ValueError, "literal URL segments"):
            generator.generate(3000, self.root / "data")
        for name, contents in before.items():
            with self.subTest(name=name):
                self.assertEqual((generated / name).read_bytes(), contents)


if __name__ == "__main__":
    unittest.main()
