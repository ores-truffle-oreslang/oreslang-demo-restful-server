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
                     "+/../outside", "+/", "@/src/lib/telemetry"):
            with self.subTest(import_path=path):
                (self.root / "src/main.ores").write_text(
                    'import module telemetry from "' + path + '";\n'
                )
                output = Path(self.temp.name) / "rejected.zip"
                with self.assertRaisesRegex(ValueError, "rooted import|malformed|retired"):
                    bundle.pack(self.root, output)
                self.assertFalse(output.exists())

    def test_unrecognized_import_declarations_fail_closed(self):
        cases = (
            'import module telemetry\n  from "+/src/lib/telemetry";\n',
            'import module telemetry from "+/src/lib/telemetry"\n',
            'import module telemetry from\n  "+/src/lib/telemetry";\n',
        )
        for source in cases:
            with self.subTest(source=source):
                (self.root / "src/main.ores").write_text(source)
                target = Path(self.temp.name) / "not-published.zip"
                with self.assertRaisesRegex(ValueError, "unsupported import declaration"):
                    bundle.pack(self.root, target)
                self.assertFalse(target.exists())

    def test_source_only_comments_and_trailing_comments_remain_valid(self):
        (self.root / "src/main.ores").write_text(
            '// import module ghost from "+/missing";\n'
            'import module telemetry from "+/src/lib/telemetry"; // commentary\n'
        )
        output, count = self.archive()
        self.assertEqual(count, 4)
        self.assertTrue(output.exists())

    def test_absolute_and_unbundled_package_imports_are_rejected(self):
        bad_imports = (
            ("/tmp/secret", "absolute filesystem import"),
            ("C:/windows/private", "absolute filesystem import"),
            (r"C:\windows\private", "absolute filesystem import"),
            (r"\\server\share\secret", "absolute filesystem import"),
            ("file:/var/tmp/secret", "absolute filesystem import"),
            ("pkg:tools/src/answer", "unbundled pkg: import"),
        )
        for path, diagnostic in bad_imports:
            with self.subTest(path=path):
                (self.root / "src/main.ores").write_text(
                    'import module unsafe from "' + path + '";\n'
                )
                target = Path(self.temp.name) / "restricted.zip"
                with self.assertRaisesRegex(ValueError, diagnostic):
                    bundle.pack(self.root, target)
                self.assertFalse(target.exists())

    def test_comments_do_not_create_false_missing_rooted_imports(self):
        (self.root / "src/main.ores").write_text(
            '// import module ghost from "+/missing";\n'
            'import module telemetry from "+/src/lib/telemetry";\n'
        )
        output, count = self.archive()
        self.assertEqual(count, 4)
        self.assertTrue(output.exists())



    def test_missing_relative_import_target_is_rejected_before_publish(self):
        output, _ = self.archive()
        original = output.read_bytes()
        (self.root / "src/main.ores").write_text(
            'import module routes from "./generated/routes";\n'
        )
        with self.assertRaisesRegex(ValueError, "relative import target missing"):
            bundle.pack(self.root, output)
        self.assertEqual(original, output.read_bytes())

    def test_relative_imports_resolve_from_importer_not_package_root(self):
        (self.root / "src/main.ores").write_text(
            'import module routes from "./generated/routes";\n'
        )
        (self.root / "src/generated/routes.ores").write_text(
            'import module telemetry from "../lib/telemetry";\n'
        )
        output, count = self.archive()
        self.assertEqual(count, 5)
        with zipfile.ZipFile(output) as archive:
            self.assertIn("src/generated/routes.ores", archive.namelist())

    def test_relative_import_cannot_escape_archive_root(self):
        (self.root / "src/main.ores").write_text(
            'import module secret from "../../outside";\n'
        )
        with self.assertRaisesRegex(ValueError, "relative import escapes"):
            self.archive()

    def test_missing_application_entrypoint_is_rejected(self):
        (self.root / "src/main.ores").unlink()
        with self.assertRaisesRegex(ValueError, "application entrypoint missing"):
            self.archive()


    def test_symlinked_submodule_parent_is_rejected(self):
        # The source root itself is not a symlink, but a middle ancestor is.
        import shutil
        source = self.root / "dependencies/spin"
        outside = Path(self.temp.name) / "outside-spin"
        shutil.move(str(source), str(outside))
        try:
            source.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable")
        with self.assertRaisesRegex(ValueError, "symlinked source directory"):
            self.archive()

    def test_hardlinked_ores_source_is_rejected(self):
        import os
        external = Path(self.temp.name) / "external.ores"
        external.write_text("pub fnc outside(): int { return 7; }")
        alias = self.root / "src/external.ores"
        try:
            os.link(external, alias)
        except (OSError, NotImplementedError):
            self.skipTest("hardlinks are unavailable")
        with self.assertRaisesRegex(ValueError, "hardlinked bundle input"):
            self.archive()

    def test_hardlinked_package_manifest_is_rejected(self):
        import os
        manifest = self.root / ".ores.pkg.toml"
        external = Path(self.temp.name) / "outside-manifest.toml"
        try:
            os.link(manifest, external)
        except (OSError, NotImplementedError):
            self.skipTest("hardlinks are unavailable")
        with self.assertRaisesRegex(ValueError, "hardlinked bundle input"):
            self.archive()

    def test_source_size_is_limited_before_reading_entire_file(self):
        original = bundle.MAX_SOURCE_BYTES
        try:
            bundle.MAX_SOURCE_BYTES = 32
            (self.root / "src/oversized.ores").write_bytes(b"x" * 4096)
            with self.assertRaisesRegex(ValueError, "exceeds bundle limit"):
                self.archive()
        finally:
            bundle.MAX_SOURCE_BYTES = original


if __name__ == "__main__":
    unittest.main()
