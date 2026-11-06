"""Host route generator must quote untrusted filesystem data path as one literal.

This executes generator publication, not compiled AttoLang guest handlers.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "wave9861_routes", ROOT / "scripts/generate-routes.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class HostDataPathLiteralAdmission(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        route = self.root / "src/routes/rest/health"
        route.mkdir(parents=True)
        (route / "get.ores").write_text("fixture")
        scoped_root = patch.object(generator, "ROOT", self.root)
        scoped_root.start()
        self.addCleanup(scoped_root.stop)

    def test_quoted_backslash_unicode_and_newline_path_never_escape_string(self):
        folder = self.root / 'data "quoted"\\literal 🥑\nnext'
        generator.generate(4444, folder)
        config_file = self.root / "src/generated/config.ores"
        routes_file = self.root / "src/generated/routes.ores"
        config = config_file.read_text()
        routes = routes_file.read_text()
        expected_literal = json.dumps(str(folder.resolve() / "baz.txt"))
        self.assertIn("return " + expected_literal + ";", config)
        self.assertIn("pub fnc port(): int { return 4444; }", config)
        self.assertEqual(config.count("pub fnc data_path()"), 1)
        self.assertEqual(config.count("define module config as"), 1)
        self.assertIn("GET /health", routes)
        self.assertNotIn("\nnext/baz.txt", config, "newline must be escaped in source")
        self.assertTrue(folder.is_dir())
        # Same pinned source and path yield byte-identical host publications.
        before = (config_file.read_bytes(), routes_file.read_bytes())
        generator.generate(4444, folder)
        self.assertEqual(
            (config_file.read_bytes(), routes_file.read_bytes()), before)


if __name__ == "__main__":
    unittest.main()
