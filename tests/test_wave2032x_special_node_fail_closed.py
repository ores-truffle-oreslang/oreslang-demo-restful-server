"""Source-owned route publisher regression: forbidden node types never republish.

These are host-side source admission tests, not compiler-backed guest proofs.
"""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "generate-routes.py"
spec = importlib.util.spec_from_file_location("wave2032x_route_generator", MODULE)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class SpecialNodePublicationFence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.routes = self.root / "src/routes/rest"
        route = self.routes / "health/get.ores"
        route.parent.mkdir(parents=True)
        route.write_text("handler fixture", encoding="utf-8")
        root_patch = patch.object(generator, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        generator.generate(8080, self.root / "data")
        self.generated = self.root / "src/generated"
        self.original = {
            name: (self.generated / name).read_bytes()
            for name in ("routes.ores", "config.ores")
        }

    def assert_unmodified(self):
        for name, content in self.original.items():
            self.assertEqual((self.generated / name).read_bytes(), content)
        self.assertEqual(list(self.generated.glob(".routes-*")), [])

    def test_symlinked_non_source_node_cannot_hide_in_route_tree(self):
        secret = self.root / "external-sentinel"
        secret.write_text("do not follow or edit", encoding="utf-8")
        link = self.routes / "health/.unrelated.bak"
        link.symlink_to(secret)
        with self.assertRaisesRegex(ValueError, "route must remain under route root"):
            generator.generate(8081, self.root / "new-data")
        self.assert_unmodified()
        self.assertEqual(secret.read_text(encoding="utf-8"), "do not follow or edit")
        link.unlink()
        generator.generate(8080, self.root / "data")
        self.assert_unmodified()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX named pipes required")
    def test_named_pipe_disguised_as_route_source_rejected_without_opening_it(self):
        pipe = self.routes / "health/post.ores"
        os.mkfifo(pipe)
        with self.assertRaisesRegex(ValueError, "regular source file"):
            generator.generate(8081, self.root / "new-data")
        self.assert_unmodified()
        pipe.unlink()
        pipe.write_text("restored regular handler", encoding="utf-8")
        generator.generate(8080, self.root / "data")
        self.assertIn('builder.route("POST"', (self.generated / "routes.ores").read_text())
        self.assertFalse((self.generated / "config.ores").is_symlink())


if __name__ == "__main__":
    unittest.main()
