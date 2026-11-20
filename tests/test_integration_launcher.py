#!/usr/bin/env python3
"""Test-only integration launcher injection; no private checkout required."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integration import Server


class TestLauncherOverride(unittest.TestCase):
    def test_external_launcher_produces_real_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher = root / "test-launcher.sh"
            launcher.write_text('#!/bin/sh\necho "Listening on http://127.0.0.1:3000"\nsleep 20\n')
            launcher.chmod(0o755)
            with patch.dict(os.environ, {"REST_SERVER_LAUNCHER": str(launcher)}, clear=False):
                with Server(root) as server:
                    self.assertIsNone(server.process.poll())

    def test_cannot_mix_native_binary_and_test_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"REST_SERVER_LAUNCHER": "/tmp/runner",
                                        "REST_SERVER_BINARY": "/tmp/native"}, clear=False):
                with self.assertRaises(ValueError):
                    Server(Path(temp))


if __name__ == "__main__":
    unittest.main()
