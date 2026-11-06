"""Host route generator must JSON-escape data paths before publishing source.

This tests deterministic generated .ores text, NOT executed AttoLang guest code.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

GENERATOR_PATH = Path(__file__).resolve().parents[1] / "scripts/generate-routes.py"
spec = importlib.util.spec_from_file_location("wave2188_routes_generator", GENERATOR_PATH)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class ConfigPathJsonEscaping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        route = self.root / "src/routes/rest/health"
        route.mkdir(parents=True)
        (route / "get.ores").write_text("route fixture")
        owner = patch.object(generator, "ROOT", self.root)
        owner.start()
        self.addCleanup(owner.stop)

    def test_unicode_quotes_and_backslash_in_data_path_are_literal(self):
        data = self.root / 'data "quote" 💠 \\ segment'
        generator.generate(4321, data)
        generated = self.root / "src/generated"
        config = (generated / "config.ores").read_text()
        routes = (generated / "routes.ores").read_text()
        expected = json.dumps(str(data.resolve() / "baz.txt"))
        self.assertIn("pub fnc data_path(): String { return " + expected + "; }", config)
        self.assertIn("pub fnc port(): int { return 4321; }", config)
        self.assertIn("GET /health", routes)
        self.assertNotIn('return "data "quote"', config)

        original = {name: (generated / name).read_bytes()
                    for name in ("config.ores", "routes.ores")}
        generator.generate(4321, data)
        self.assertEqual(
            {name: (generated / name).read_bytes() for name in original},
            original,
        )
        generator.generate(65535, data)
        self.assertEqual((generated / "routes.ores").read_bytes(), original["routes.ores"])
        self.assertIn("return 65535;", (generated / "config.ores").read_text())

    def test_invalid_port_keeps_published_paths_and_no_staging_files(self):
        data = self.root / 'data "quote" 💠 \\ segment'
        generator.generate(1, data)
        generated = self.root / "src/generated"
        before = [(generated / name).read_bytes() for name in ("config.ores", "routes.ores")]
        with self.assertRaises(ValueError):
            generator.generate(65536, data)
        self.assertEqual(
            [(generated / name).read_bytes() for name in ("config.ores", "routes.ores")],
            before,
        )
        self.assertEqual(list(generated.glob(".routes-*")), [])


if __name__ == "__main__":
    unittest.main()
