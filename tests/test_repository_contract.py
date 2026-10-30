#!/usr/bin/env python3
"""Public, offline repository pin contract (no private dependency clone required)."""
import configparser
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SUBMODULES = {
    "dependencies/spin": "https://github.com/ores-truffle-oreslang/oreslang-spin.git",
    "dependencies/otel": "https://github.com/ores-truffle-oreslang/oreslang-otel.git",
}

class RepositoryContract(unittest.TestCase):
    def test_compiler_ref_is_immutable_full_sha(self):
        ref = (ROOT / "SOURCE_REF").read_text(encoding="utf-8").strip()
        self.assertRegex(ref, r"\A[0-9a-f]{40}\Z")
        self.assertNotEqual(ref, "0" * 40)

    def test_submodule_gitlinks_and_remote_allowlist(self):
        config = configparser.ConfigParser()
        with (ROOT / ".gitmodules").open(encoding="utf-8") as fh:
            config.read_file(fh)
        observed = {
            config[section]["path"]: config[section]["url"]
            for section in config.sections()
        }
        self.assertEqual(observed, EXPECTED_SUBMODULES)
        # ls-tree reads the checked-out commit's gitlinks, not mutable
        # working-tree contents or moving submodule branch names.
        for path in EXPECTED_SUBMODULES:
            raw = subprocess.check_output(
                ["git", "-C", str(ROOT), "ls-tree", "HEAD", "--", path],
                text=True,
            ).strip()
            match = re.fullmatch(r"160000 commit ([0-9a-f]{40})\t(.+)", raw)
            self.assertIsNotNone(match, (path, raw))
            self.assertEqual(match.group(2), path)

    def test_compiler_rebuilds_from_clean_outputs(self):
        for script in ("setup.sh", "build-native.sh"):
            content = (ROOT / "scripts" / script).read_text(encoding="utf-8")
            self.assertIn("-DskipTests clean package dependency:build-classpath", content)

    def test_scripts_use_commit_addressed_default_cache(self):
        for file in ("setup.sh", "run-server.sh", "test.sh", "build-native.sh"):
            script = (ROOT / "scripts" / file).read_text(encoding="utf-8")
            self.assertIn(".cache/compiler-$expected", script, file)
            self.assertTrue("SOURCE_REF" in script or "ores_read_exact_source_ref" in script, file)
            self.assertIn("check-deps.sh", script, file)
        # The shared guard helper reads the immutable pin for callers that
        # intentionally avoid duplicating direct SOURCE_REF parsing.
        guard = (ROOT / "scripts/lib/guards.sh").read_text(encoding="utf-8")
        self.assertIn("SOURCE_REF", guard)

if __name__ == "__main__":
    unittest.main()
