#!/usr/bin/env python3
"""Opt-in REAL AttoLang HTTP/1.1 adversarial integration, not host-only evidence.

This starts the same pinned Java/Spin guest server as tests/integration.py.
Do not run against a production server: the test creates a disposable resource.
Requires an authorized local checkout of the private compiler and dependencies.
"""
import re
import socket
import tempfile
from pathlib import Path

from integration import Server, request

RESOURCE = "/foo/bar/baz"
DENIAL = {400, 408, 413, 414, 422, 431, 505}
STATUS_RE = re.compile(rb"(?m)^HTTP/1\.[01] ([0-9]{3})(?:[ \t][^\r\n]*)?\r?$")


def wire(headers, body=b"", *, method="POST", path=RESOURCE, version="HTTP/1.1"):
    return (f"{method} {path} {version}\r\nHost: 127.0.0.1\r\n"
            "Connection: close\r\n").encode("ascii") + headers + b"\r\n" + body


def response_statuses(payload):
    """Find every HTTP response status, including responses from request smuggling."""
    return [int(value) for value in STATUS_RE.findall(payload)]


def transmit(port, payload):
    """Bound time and received bytes; distinguish a closed socket from a hang."""
    buffer = bytearray()
    closed = False
    with socket.create_connection(("127.0.0.1", port), timeout=4) as conn:
        conn.settimeout(5)
        conn.sendall(payload)
        conn.shutdown(socket.SHUT_WR)
        while len(buffer) < 32768:
            try:
                chunk = conn.recv(min(4096, 32768 - len(buffer)))
            except ConnectionResetError:
                closed = True
                break
            except socket.timeout:
                if response_statuses(bytes(buffer)):
                    break
                raise AssertionError("server hung without an HTTP response or EOF")
            if not chunk:
                closed = True
                break
            buffer.extend(chunk)
    if len(buffer) >= 32768:
        raise AssertionError("malformed request yielded unexpectedly large response")
    return bytes(buffer), closed


def deny(port, name, payload, allowed=DENIAL):
    """A rejection or clean transport close passes; capacity 503 is inconclusive."""
    for _ in range(5):
        response, closed = transmit(port, payload)
        statuses = response_statuses(response)
        if statuses == [503]:
            # No admission occurred. Retry instead of treating overload as a pass.
            continue
        if statuses:
            if any(status not in allowed for status in statuses):
                raise AssertionError(f"{name}: unexpected status sequence {statuses!r}")
        elif not closed:
            raise AssertionError(f"{name}: neither rejected nor closed the transport")
        return statuses or ["closed"]
    raise AssertionError(f"{name}: repeated capacity 503; no parser evidence")


def cases():
    """Named boundary violations, using only the disposable resource/loopback."""
    short = b"Content-Type: text/plain\r\n"
    yield "conflicting duplicate content length", wire(
        short + b"Content-Length: 3\r\nContent-Length: 4\r\n", b"abc")
    yield "identical duplicate content length", wire(
        short + b"Content-Length: 3\r\nContent-Length: 3\r\n", b"abc")
    yield "ambiguous transfer encoding plus content length", wire(
        short + b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\n",
        b"0\r\n\r\nGET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    yield "unsupported transfer encoding", wire(
        short + b"Transfer-Encoding: gzip\r\n", b"garbage")
    yield "invalid content length", wire(
        short + b"Content-Length: -1\r\n", b"xyz")
    yield "invalid chunk size", wire(
        short + b"Transfer-Encoding: chunked\r\n", b"Z\r\nabc\r\n0\r\n\r\n")
    yield "nul in header value", wire(
        b"X-Test: a\x00b\r\nContent-Length: 3\r\n" + short, b"abc")
    yield "obs-fold header", wire(
        b"X-Test: foo\r\n bar\r\nContent-Length: 3\r\n" + short, b"abc")
    yield "bare LF framing", (
        b"POST /foo/bar/baz HTTP/1.1\nHost: 127.0.0.1\n"
        b"Connection: close\nContent-Type: text/plain\nContent-Length: 3\n\nabc")
    yield "truncated fixed-length body", wire(
        short + b"Content-Length: 12\r\n", b"abc")
    yield "invalid text utf8", wire(
        short + b"Content-Length: 1\r\n", b"\xff")
    yield "declared body above 64 KiB", wire(
        short + b"Content-Length: 65537\r\n")
    yield "decoded chunked body above 64 KiB", wire(
        short + b"Transfer-Encoding: chunked\r\n",
        b"10000\r\n" + b"x" * 65536 + b"\r\n1\r\ny\r\n0\r\n\r\n")
    yield "unknown HTTP version", wire(
        b"", method="GET", path="/health", version="HTTP/1.2")


def main():
    with tempfile.TemporaryDirectory(prefix="atto-rest-http11-negative-") as tmp:
        root = Path(tmp)
        with Server(root) as server:
            status, _, _ = request(
                server.port, "POST", RESOURCE, b"untouched sentinel",
                {"Content-Type": "text/plain"},
            )
            if status != 201:
                raise AssertionError(f"fixture creation failed: {status}")
            resource = root / "data/baz.txt"
            before = resource.read_bytes()
            count = 0
            for name, payload in cases():
                outcome = deny(server.port, name, payload)
                if resource.read_bytes() != before:
                    raise AssertionError(f"{name}: rejected request mutated persisted bytes")
                code, body, _ = request(server.port, "GET", RESOURCE)
                if (code, body) != (200, before):
                    raise AssertionError(f"{name}: subsequent GET was not healthy")
                code, _, _ = request(server.port, "GET", "/health")
                if code != 200:
                    raise AssertionError(f"{name}: server did not recover: {code}")
                count += 1
                print(f"PASS {name}: {outcome}")
            print(f"PASS: {count} real guest HTTP/1.1 adversarial cases")
            

if __name__ == "__main__":
    main()
