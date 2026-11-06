"""Reject an intermediate symlinked route ancestor before any new publication.

Host-side route generator test; this is not compiled AttoLang E2E evidence.
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate-routes.py"
spec = importlib.util.spec_from_file_location("wave2031_route_generator", SCRIPT)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class RouteAncestorSymlinkAdmission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        leaf = self.root / "src/routes/rest/health"
        leaf.mkdir(parents=True)
        (leaf / "get.ores").write_text("host route fixture")
        ctx = patch.object(generator, "ROOT", self.root)
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_intermediate_routes_symlink_rejected_with_last_good_output_unchanged(self):
        generator.generate(3000, self.root / "data")
        generated = self.root / "src/generated"
        previous = {name: (generated / name).read_bytes()
                    for name in ("routes.ores", "config.ores")}
        routes = self.root / "src/routes"
        outside = self.root / "external-routes"
        routes.rename(outside)
        try:
            routes.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("filesystem cannot make directory symbolic links")

        with self.assertRaisesRegex(ValueError, "route parent must be a real directory"):
            generator.generate(3001, self.root / "different-data")
        for name, contents in previous.items():
            self.assertEqual((generated / name).read_bytes(), contents)
        self.assertFalse((self.root / "different-data").exists())

    def test_missing_intermediate_routes_dir_fails_before_publication(self):
        routes = self.root / "src/routes"
        outside = self.root / "removed-routes"
        routes.rename(outside)
        with self.assertRaisesRegex(ValueError, "route parent must be a real directory"):
            generator.generate(3000, self.root / "data")
        self.assertFalse((self.root / "src/generated/routes.ores").exists())


if __name__ == "__main__":
    unittest.main()
