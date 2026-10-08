#!/usr/bin/env python3
"""Real HTTP, curl workflow, persistence and generated route validation."""
import http.client
import importlib.util
import json
import os
import re
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
        native = os.environ.get("REST_SERVER_BINARY")
        command = [native] if native else [str(ROOT / "scripts/run-server.sh")]
        if native and os.environ.get("REST_EXECUTION_MODE"):
            command.append("--mode=" + os.environ["REST_EXECUTION_MODE"])
        child_env = dict(self.env)
        if native:
            # Prove the application does not discover Java/Python/Git/Maven from
            # PATH or use our compiler checkout. The Python/curl test driver is
            # outside this deployment environment.
            for key in ["JAVA_HOME", "ORES_JAVA", "ORESLANG_SOURCE_DIR", "CLASSPATH"]:
                child_env.pop(key, None)
            child_env["PATH"] = "/nonexistent"
        self.process = subprocess.Popen(command, env=child_env,
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
    with tempfile.TemporaryDirectory(prefix='ores rest "test" -') as tmp:
        directory = Path(tmp)
        with Server(directory) as server:
            curls = subprocess.run([str(ROOT / "scripts/curl-10.sh")],
                           env=dict(server.env, BASE_URL=f"http://127.0.0.1:{server.port}"),
                           check=True, timeout=90, capture_output=True, text=True)
            print(curls.stdout, end="")
            curl_rows = [line for line in curls.stdout.splitlines() if " -> " in line]
            assert len(curl_rows) == 10
            assert [re.search(r"actor=(\w+)", line)[1] for line in curl_rows] == ["shared", "isolated", "shared", "isolated", "isolated", "shared", "isolated", "shared", "isolated", "isolated"]
            curl_ids = [re.search(r"request_id=([0-9a-f-]{36})", line)[1] for line in curl_rows]
            assert len(set(curl_ids)) == 10
            assert all(re.search(r"time=[0-9.]+s retries=\d+", line) for line in curl_rows)
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

            # A real response must produce one completed request span and the
            # correct actor/dispatch hierarchy, including framework responses.
            traces = []
            for method, path, expected, actor in [
                ("GET", "/health", 200, True),
                ("HEAD", "/health", 200, True),
                ("GET", "/missing?secret=do-not-log", 404, False),
                ("POST", "/health", 405, False),
                ("OPTIONS", "/health", 204, False),
            ]:
                status, _, headers = request(server.port, method, path)
                assert status == expected
                trace = headers["X-ores-trace-id"]
                assert trace == "ores-trace-" + headers["X-request-id"]
                assert headers["X-actor-mode"] == ("shared" if actor else "supervisor")
                traces.append((trace, expected, actor))
            for _ in range(100):
                server.log.seek(0)
                text = server.log.read()
                rows = [json.loads(line) for line in text.splitlines() if line.startswith('{"schema":')]
                counts = {r['ores_trace_id'] for r in rows if r['signal'] == 'metric' and r['name'] == 'http.server.requests'}
                if all(trace in counts for trace, _, _ in traces):
                    break
                time.sleep(0.02)
            assert 'do-not-log' not in text
            for request_id, line in zip(curl_ids, curl_rows):
                mode = re.search(r'actor=(\w+)', line)[1]
                claims = [r for r in rows if r['signal'] == 'log' and r['ores_trace_id'] == 'ores-trace-' + request_id and r['body'].startswith('actor.claim ')]
                assert len(claims) == 1
                assert 'mode=' + mode in claims[0]['body']
                assert 'request_id=' + request_id in claims[0]['body']
                assert claims[0]['timestamp_unix_ms'] > 0
            assert len({t[0] for t in traces}) == len(traces)
            for trace, expected, has_actor in traces:
                spans = [r for r in rows if r['signal'] == 'span' and r['ores_trace_id'] == trace]
                roots = [r for r in spans if r['name'] == 'http.server.request']
                assert len(roots) == 1, spans
                root = roots[0]
                assert root['http_status'] == expected and root['duration_ns'] >= 0
                assert root['outcome'] == 'ok'
                children = [r for r in spans if r['parent_span_id'] == root['span_id']]
                assert {r['name'] for r in children} == ({'routing.dispatch', 'actor.handle'} if has_actor else {'routing.dispatch'})
                assert all(0 <= r['duration_ns'] <= root['duration_ns'] for r in children)
                metrics = [r for r in rows if r['signal'] == 'metric' and r['ores_trace_id'] == trace]
                duration = [r for r in metrics if r['name'] == 'http.server.request.duration']
                assert len(duration) == 1 and duration[0]['value'] == root['duration_ns']
                checks += 1

            # Force a handler I/O failure without adding a test-only HTTP route.
            resource = directory / 'data/baz.txt'
            saved = resource.read_text()
            resource.unlink()
            resource.mkdir()
            status, _, headers = request(server.port, 'GET', '/foo/bar/baz')
            assert status == 500
            failed_trace = headers['X-ores-trace-id']
            for _ in range(100):
                server.log.seek(0)
                rows = [json.loads(line) for line in server.log.read().splitlines() if line.startswith('{"schema":')]
                failures = [r for r in rows if r['signal'] == 'metric' and r['ores_trace_id'] == failed_trace and r['name'] == 'http.server.errors']
                if failures:
                    break
                time.sleep(0.02)
            assert len(failures) == 1 and failures[0]['value'] == 1
            assert any(r['signal'] == 'span' and r['name'] == 'actor.handle' and r['ores_trace_id'] == failed_trace and r['outcome'] == 'error' for r in rows)
            resource.rmdir()
            resource.write_text(saved)
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
