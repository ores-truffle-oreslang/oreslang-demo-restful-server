"""HOST-ONLY smoke-script contract tests using a tiny fake loopback server.

This fake only tests the Bash probe itself. It is NOT AttoLang guest execution,
serial HTTP actor lowering, cancellation evidence or backend parity.
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/read-only-http-transport-smoke.sh"


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        config = self.server.config
        if self.path == "/health":
            status = 200
            body = (b'{"status":"ok"}' if not config.get("bad_body")
                    else b'{"status":"unhealthy"}')
        else:
            status = (200 if config.get("bad_missing_route") else 404)
            body = b"missing"
        self.send_response(status)
        if not config.get("no_request_id"):
            self.send_header("X-Request-Id",
                             str(uuid.UUID(int=1)) if config.get("duplicate_id")
                             else str(uuid.uuid4()))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(503 if self.server.config.get("bad_head") else 200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def execute(base_url, config=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
    server.config = config or {}
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        env = dict(os.environ)
        env["BASE_URL"] = base_url.format(port=server.server_port)
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


class SmokeHarnessTests(unittest.TestCase):
    def test_valid_synthetic_responses_prove_script_only(self):
        result = execute("http://127.0.0.1:{port}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LIVE_HTTP_LOOPBACK=PASS", result.stdout)
        self.assertIn("FIVE_BACKEND_GUEST_PARITY=NOT_VERIFIED", result.stdout)

    def test_duplicate_request_ids_are_rejected(self):
        result = execute("http://127.0.0.1:{port}", {"duplicate_id": True})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("request identity reused", result.stderr)

    def test_missing_request_id_fails(self):
        result = execute("http://127.0.0.1:{port}", {"no_request_id": True})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing/invalid request identity", result.stderr)

    def test_wrong_body_fails(self):
        result = execute("http://127.0.0.1:{port}", {"bad_body": True})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("health body mismatch", result.stderr)

    def test_wrong_head_or_404_fails(self):
        for options in ({"bad_head": True}, {"bad_missing_route": True}):
            with self.subTest(options=options):
                result = execute("http://127.0.0.1:{port}", options)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("LIVE_HTTP_LOOPBACK=PASS", result.stdout)

    def test_nonloopback_and_invalid_port_fail_before_connecting(self):
        for base in ("http://localhost:12345", "http://0.0.0.0:12345",
                     "https://127.0.0.1:12345", "http://127.0.0.1:0",
                     "http://127.0.0.1:65536",
                     "http://127.0.0.1:12345/health",
                     "http://127.0.0.1:12345@evil.example"):
            with self.subTest(base=base):
                result = subprocess.run(
                    ["bash", str(SCRIPT)], cwd=ROOT,
                    env=dict(os.environ, BASE_URL=base),
                    capture_output=True, text=True, timeout=5,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("LIVE_HTTP_LOOPBACK=PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
