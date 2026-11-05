"""Source-owned route generator tests. Host-only, not AttoLang guest parity."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "scripts/generate-routes.py"
spec = importlib.util.spec_from_file_location("route_generator_wave31", source)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class RouteCaseAndEmissionWave31(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.routes = self.root / "src/routes/rest"
        (self.routes / "v1").mkdir(parents=True)
        (self.routes / "v1/get.ores").write_text("fixture", encoding="utf-8")
        active = patch.object(generator, "ROOT", self.root)
        active.start()
        self.addCleanup(active.stop)

    def test_uppercase_method_is_rejected_before_publication(self):
        (self.routes / "v1/GET.ores").write_text("fixture", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsupported method filename"):
            generator.generate(3000, self.root / "data")
        self.assertFalse((self.root / "src/generated/routes.ores").exists())

    def test_nested_literal_segment_is_escaped_and_kept_under_root(self):
        (self.routes / "v1/team_b-2").mkdir()
        (self.routes / "v1/team_b-2/post.ores").write_text("fixture", encoding="utf-8")
        generator.generate(3000, self.root / "data")
        result = (self.root / "src/generated/routes.ores").read_text()
        self.assertIn('http_routing.path("/v1/team_b-2"', result)
        self.assertIn('http_routing.literal("team_b-2")', result)
        self.assertLess(result.index("GET /v1"), result.index("POST /v1/team_b-2"))

    def test_existing_valid_output_survives_route_discovery_failure(self):
        generated = self.root / "src/generated"
        generated.mkdir()
        original = generated / "routes.ores"
        original.write_text("prior known-good table", encoding="utf-8")
        (self.routes / "v1/Bad.Part").mkdir()
        (self.routes / "v1/Bad.Part/post.ores").write_text("invalid", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "literal URL segments"):
            generator.generate(3000, self.root / "data")
        self.assertEqual(original.read_text(), "prior known-good table")
        self.assertFalse((generated / "config.ores").exists())


if __name__ == "__main__":
    unittest.main()
