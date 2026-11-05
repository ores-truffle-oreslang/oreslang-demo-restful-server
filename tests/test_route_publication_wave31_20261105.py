"""Host-only route-generator checks; no AttoLang guest or HTTP runtime execution."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

GENERATOR = Path(__file__).resolve().parents[1] / "scripts/generate-routes.py"
SPEC = importlib.util.spec_from_file_location("wave31_route_generator", GENERATOR)
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


class RoutePublicationWave31(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.routes = self.root / "src/routes/rest"
        self.routes.mkdir(parents=True)
        self.patch = patch.object(generator, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def add(self, route, method):
        path = self.routes / route / (method + ".ores")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("route fixture\n", encoding="utf-8")
        return path

    def test_nested_literal_routes_and_method_order_are_stable(self):
        self.add("users/me", "post")
        self.add("health", "get")
        generator.generate(8080, self.root / "data")
        source = (self.root / "src/generated/routes.ores").read_text()
        self.assertIn('builder.route("GET"', source)
        self.assertIn('builder.route("POST"', source)
        self.assertIn('http_routing.literal("users")', source)
        self.assertIn('http_routing.literal("me")', source)
        self.assertLess(source.index("GET /health"), source.index("POST /users/me"))

    def test_invalid_case_or_nonliteral_route_keeps_existing_artifacts(self):
        self.add("ok", "get")
        generator.generate(8080, self.root / "data")
        generated = self.root / "src/generated"
        before = {n: (generated / n).read_bytes() for n in ("routes.ores", "config.ores")}
        bad = self.add("another", "Get")
        with self.assertRaisesRegex(ValueError, "unsupported method filename"):
            generator.generate(8080, self.root / "data")
        bad.unlink()
        self.add("bad%2Fsegment", "get")
        with self.assertRaisesRegex(ValueError, "literal URL segments"):
            generator.generate(8080, self.root / "data")
        self.assertEqual(before, {n: (generated / n).read_bytes() for n in before})

    def test_generated_leaf_symlinks_do_not_overwrite_external_data(self):
        self.add("health", "get")
        generated = self.root / "src/generated"
        generated.mkdir()
        external = self.root / "outside.txt"
        external.write_text("preserve this")
        (generated / "routes.ores").symlink_to(external)
        generator.generate(8080, self.root / "data")
        self.assertEqual(external.read_text(), "preserve this")
        self.assertFalse((generated / "routes.ores").is_symlink())
        self.assertIn("GET /health", (generated / "routes.ores").read_text())

    def test_invalid_port_fails_before_any_publication(self):
        self.add("health", "get")
        for port in (0, 65536, -1):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "port"):
                generator.generate(port, self.root / "data")
            self.assertFalse((self.root / "src/generated").exists())


if __name__ == "__main__":
    unittest.main()
