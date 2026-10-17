"""Offline native bundle checks: root manifest, +/ modules, config redaction."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "embedded_app_builder", ROOT / "scripts/build_embedded_application.py"
)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(builder)


class NativeEmbeddedProjectRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "app"
        self.root.mkdir()
        for name in builder.SOURCE_DIRECTORIES:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / builder.MANIFEST).write_text(
            'schema_version = "1"\n[project]\nroot = "."\n', encoding="utf-8"
        )
        (self.root / "src/main.ores").write_text(
            'import module store from "+/lib/store";\n', encoding="utf-8"
        )
        (self.root / "src/generated").mkdir()
        (self.root / builder.CONFIG).write_text(
            'define module config as\n'
            ' pub fnc data_path(): String { return "/build-machine/private/data.txt"; }\n'
            'end\n', encoding="utf-8"
        )
        (self.root / "lib/store.ores").write_text(
            'import module config from "+/src/generated/config";\n', encoding="utf-8"
        )
        (self.root / "lib/telemetry.ores").write_text(
            'define module telemetry as\nend\n', encoding="utf-8"
        )
        (self.root / "dependencies/spin/src/spin.ores").write_text(
            'define module spin as\nend\n', encoding="utf-8"
        )
        self.output = Path(self.temp.name) / "artifact.zip"

    def test_complete_root_anchor_is_embedded_and_config_scrubbed(self):
        builder.build_archive(self.root, self.output)
        with zipfile.ZipFile(self.output) as archive:
            names = set(archive.namelist())
            self.assertTrue({
                ".ores.pkg.toml", "src/main.ores", "src/generated/config.ores",
                "lib/store.ores", "lib/telemetry.ores",
                "dependencies/spin/src/spin.ores"
            }.issubset(names))
            self.assertEqual(builder.REDACTED_CONFIG, archive.read(builder.CONFIG))
            self.assertNotIn(b"/build-machine/private", archive.read(builder.CONFIG))
            self.assertIn(b"+/lib/store", archive.read("src/main.ores"))
            self.assertIn(b"+/src/generated/config", archive.read("lib/store.ores"))

    def test_rejects_missing_project_manifest_without_partial_zip(self):
        (self.root / builder.MANIFEST).unlink()
        with self.assertRaisesRegex(ValueError, "manifest"):
            builder.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_missing_lib_directory(self):
        (self.root / "lib/store.ores").unlink()
        (self.root / "lib/telemetry.ores").unlink()
        (self.root / "lib").rmdir()
        with self.assertRaisesRegex(ValueError, "lib"):
            builder.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_symlinked_project_source(self):
        actual = Path(self.temp.name) / "outside.ores"
        actual.write_text('define module injected as\nend\n', encoding="utf-8")
        link = self.root / "lib/injected.ores"
        try:
            link.symlink_to(actual)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlink unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            builder.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_symlinked_source_directory(self):
        source = self.root / "lib"
        backup = self.root / "lib-real"
        source.rename(backup)
        try:
            source.symlink_to(backup, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlink unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "symlinked source directory"):
            builder.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_archive_bytes_are_reproducible(self):
        builder.build_archive(self.root, self.output)
        first = self.output.read_bytes()
        builder.build_archive(self.root, self.output)
        self.assertEqual(first, self.output.read_bytes())

    def test_rejects_symlinked_manifest(self):
        original = self.root / builder.MANIFEST
        backup = self.root / "outside.toml"
        original.rename(backup)
        try:
            original.symlink_to(backup)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlink unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            builder.build_archive(self.root, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
