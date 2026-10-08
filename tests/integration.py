#!/usr/bin/env python3
"""Real HTTP, curl workflow, persistence and generated route validation."""
import http.client
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]

def request(port, method, path, body=None, headers=None):
    for _ in range(50):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=12)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            result = response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()
        if result[0] != 503:
            return result
        time.sleep(0.02)
    raise AssertionError("server admission did not recover")

class Server:
    def __init__(self, directory):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.log = (directory / "server.log").open("w+")
        self.env = dict(os.environ, PORT=str(self.port), DATA_DIR=str(directory / "data"))
        self.process = subprocess.Popen([str(ROOT / "scripts/run-server.sh")], env=self.env,
                                        stdout=self.log, stderr=self.log)
    def __enter__(self):
        for _ in range(300):
            self.log.seek(0)
            output = self.log.read()
            if "Listening on" in output:
                return self
            if self.process.poll() is not None:
                self.stop()
                raise AssertionError(output)
            time.sleep(0.05)
        self.stop()
        raise AssertionError("server startup timed out")
    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.log.close()
    def __exit__(self, *_):
        self.stop()

def main():
    checks = 0
    with tempfile.TemporaryDirectory(prefix="ores-rest-test-") as tmp:
        directory = Path(tmp)
        with Server(directory) as server:
            subprocess.run([str(ROOT / "scripts/curl-10.sh")],
                           env=dict(server.env, BASE_URL=f"http://127.0.0.1:{server.port}"),
                           check=True, timeout=45)
            checks += 10
            for method, path, body, headers, status in [
                ("GET", "/not-found", None, {}, 404),
                ("POST", "/health", None, {}, 405),
                ("OPTIONS", "/foo/bar/baz", None, {}, 204),
                ("POST", "/foo/bar/baz", "no media type", {}, 415),
                ("POST", "/foo/bar/baz", "", {"Content-Type": "text/plain"}, 400),
                ("PATCH", "/foo/bar/baz", "append", {"Content-Type": "text/plain"}, 404),
                ("PUT", "/foo/bar/baz", "x" * 65537, {"Content-Type": "text/plain"}, 413),
                ("GET", "/%2e%2e/secret", None, {}, 404),
            ]:
                actual, payload, response_headers = request(server.port, method, path, body, headers)
                assert actual == status, (method, path, status, actual, payload)
                if method == "OPTIONS":
                    assert {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} <= set(response_headers["Allow"].split(", "))
                checks += 1
            status, body, headers = request(server.port, "POST", "/foo/bar/baz", "survives restart", {"Content-Type": "text/plain"})
            assert (status, body) == (201, b"survives restart")
            assert headers["X-actor-mode"] == "isolated", headers
            assert headers["Location"] == "/foo/bar/baz"
            checks += 1
            status, body, headers = request(server.port, "HEAD", "/foo/bar/baz")
            assert (status, body) == (200, b"")
            assert headers["X-actor-mode"] == "shared", headers
            checks += 1
        with Server(directory) as server:
            assert request(server.port, "GET", "/foo/bar/baz")[:2] == (200, b"survives restart")
            checks += 1
        assert (directory / "data/baz.txt").read_text() == "survives restart"
        checks += 1
    # The generator derives paths, rejects invalid method files, and cannot use
    # symlinked route sources to import outside the root.
    spec = importlib.util.spec_from_file_location("route_generator", ROOT / "scripts/generate-routes.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "foo/bar/baz/get.ores"
        path.parent.mkdir(parents=True)
        path.touch()
        assert generator.discover(root)[0][:2] == ("GET", ("foo", "bar", "baz"))
        checks += 1
        path.rename(path.with_name("unknown.ores"))
        try:
            generator.discover(root)
            raise AssertionError("unsupported method accepted")
        except ValueError:
            checks += 1
        path.with_name("unknown.ores").unlink()
        path.symlink_to(Path(__file__).resolve())
        try:
            generator.discover(root)
            raise AssertionError("symlinked route accepted")
        except ValueError:
            checks += 1
    print(f"PASS: {checks} integration and route-generation checks")

if __name__ == "__main__":
    main()
