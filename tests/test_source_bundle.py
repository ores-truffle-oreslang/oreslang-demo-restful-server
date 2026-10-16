#!/usr/bin/env python3
"""Tests for the exact source-bundling routine used by native builds."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/package-ores-sources.py"
spec = importlib.util.spec_from_file_location("ores_source_bundle", SCRIPT)
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class SourceBundle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        for directory in bundle.SOURCE_DIRS:
            (self.root / directory).mkdir(parents=True)
        (self.root / ".ores.pkg.toml").write_text('schema_version = "1"\n')
        (self.root / "src/main.ores").write_text(
            'import module telemetry from "+/src/lib/telemetry";\n')
        (self.root / "src/lib").mkdir()
        (self.root / "src/lib/telemetry.ores").write_text("define module telemetry end\n")
        (self.root / "src/generated").mkdir()
        (self.root / "src/generated/config.ores").write_text("SECRET BUILD PATH")
        (self.root / "src/.do-not-embed.env").write_text("SECRET TOKEN")

    def archive(self, name="bundle.zip"):
        output = Path(self.temp.name) / name
        return output, bundle.pack(self.root, output)

    def test_manifest_and_relative_import_sources_survive_pack(self):
        output, count = self.archive()
        self.assertEqual(count, 4)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertEqual(archive.read(".ores.pkg.toml"), b'schema_version = "1"\n')
            self.assertIn(b"+/src/lib/telemetry", archive.read("src/main.ores"))
            self.assertIn("src/lib/telemetry.ores", names)
            self.assertNotIn("src/.do-not-embed.env", names)
            self.assertEqual(archive.read("src/generated/config.ores"), bundle.CONFIG_PLACEHOLDER)
            for name in names:
                self.assertEqual(archive.getinfo(name).date_time, bundle.ZIP_EPOCH)
                self.assertFalse(name.startswith("/") or ".." in Path(name).parts)

    def test_repeatable_zip_is_byte_identical(self):
        first, _ = self.archive("a.zip")
        second, _ = self.archive("b.zip")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_missing_manifest_is_rejected_and_existing_bundle_is_preserved(self):
        output, _ = self.archive()
        previous = output.read_bytes()
        (self.root / ".ores.pkg.toml").unlink()
        with self.assertRaisesRegex(ValueError, "manifest"):
            bundle.pack(self.root, output)
        self.assertEqual(output.read_bytes(), previous)

    def test_missing_source_directory_is_rejected(self):
        (self.root / "dependencies/otel/src").rmdir()
        with self.assertRaisesRegex(ValueError, "missing source directory"):
            self.archive()

    def test_manifest_symlink_is_rejected(self):
        manifest = self.root / ".ores.pkg.toml"
        target = self.root / "other.toml"
        target.write_text('schema_version = "1"\n')
        manifest.unlink()
        try:
            manifest.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.archive()

    def test_source_symlink_is_rejected(self):
        target = self.root / "src/main.ores"
        try:
            (self.root / "src/sneaky.ores").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.archive()

    def test_directory_symlink_is_rejected(self):
        target = self.root / "src/lib"
        try:
            (self.root / "src/shortcut").symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.archive()

    def test_size_limits_fail_closed_without_output(self):
        original = bundle.MAX_SOURCE_BYTES
        try:
            bundle.MAX_SOURCE_BYTES = 8
            output = Path(self.temp.name) / "oversize.zip"
            with self.assertRaisesRegex(ValueError, "exceeds bundle limit"):
                bundle.pack(self.root, output)
            self.assertFalse(output.exists())
        finally:
            bundle.MAX_SOURCE_BYTES = original


    def test_missing_rooted_target_rejects_native_bundle_without_overwriting_previous(self):
        output, _ = self.archive()
        original = output.read_bytes()
        (self.root / "src/main.ores").write_text(
            'import module telemetry from "+/src/lib/missing";\n'
        )
        with self.assertRaisesRegex(ValueError, "rooted import target missing"):
            bundle.pack(self.root, output)
        self.assertEqual(original, output.read_bytes())

    def test_malformed_root_marker_and_traversal_are_rejected(self):
        for path in ("+src/lib/telemetry", "+/src/../src/lib/telemetry",
                     "+/../outside", "+/"):
            with self.subTest(import_path=path):
                (self.root / "src/main.ores").write_text(
                    'import module telemetry from "' + path + '";\n'
                )
                output = Path(self.temp.name) / "rejected.zip"
                with self.assertRaisesRegex(ValueError, "rooted import|malformed"):
                    bundle.pack(self.root, output)
                self.assertFalse(output.exists())

    def test_comments_do_not_create_false_missing_rooted_imports(self):
        (self.root / "src/main.ores").write_text(
            '// import module ghost from "+/missing";\n'
            'import module telemetry from "+/src/lib/telemetry";\n'
        )
        output, count = self.archive()
        self.assertEqual(count, 4)
        self.assertTrue(output.exists())



if __name__ == "__main__":
    unittest.main()
