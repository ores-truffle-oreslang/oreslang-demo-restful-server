#!/usr/bin/env python3
"""End-to-end logging contract: real HTTP, OTel stdout, core shutdown stderr.

Run after scripts/setup.sh using the pinned compiler and initialized submodules.
The core diagnostic recorder is opt-in; this test checks CLI overrides of disabled environment defaults.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

from integration import Server

ROOT = Path(__file__).resolve().parents[1]


def records(log_path):
    result = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.startswith('{"schema":'):
            item = json.loads(line)  # Reject malformed structured output.
            if isinstance(item, dict):
                result.append(item)
    return result


def main():
    with tempfile.TemporaryDirectory(prefix="ores-core-log-e2e-") as tmp:
        root = Path(tmp)
        # The launcher reads these once; do not enable logging globally in tests.
        # Force CLI parsing to override environment defaults. If the launcher
        # drops --core-perf/--core-debug, the required core JSONL is absent.
        with patch.dict(os.environ, {"ORES_CORE_PERF": "false",
                                     "ORES_CORE_DEBUG": "false"}):
            with Server(root, extra_args=("--core-perf", "--core-debug")) as server:
                result = subprocess.run(
                    [str(ROOT / "scripts/curl-10.sh")],
                    env=dict(server.env, BASE_URL=f"http://127.0.0.1:{server.port}"),
                    capture_output=True, text=True, check=True, timeout=120)
                assert "PASS: 10 REST requests" in result.stdout, result.stdout
                # Ensure userland records are exported before SIGTERM; the
                # core diagnostic records deliberately export *at* shutdown.
                for _ in range(100):
                    server.log.flush()
                    rows = records(root / "server.log")
                    roots = [r for r in rows
                             if r.get("schema") == "oreslang-otel.v1"
                             and r.get("signal") == "span"
                             and r.get("name") == "http.server.request"]
                    if len(roots) >= 10:
                        break
                    time.sleep(0.05)
                assert len(roots) >= 10, "Missing userland request spans"
            # Server.__exit__ sends SIGTERM and waits for graceful shutdown.
        rows = records(root / "server.log")
        perf = [r for r in rows if r.get("schema") == "ores-core-perf.v1"]
        debug = [r for r in rows if r.get("schema") == "ores-core-debug.v1"]
        otel = [r for r in rows if r.get("schema") == "oreslang-otel.v1"]

        assert perf and debug and otel, "Missing one or more logging schemas"
        assert len([r for r in perf if r.get("kind") == "begin"]) == 1
        assert len([r for r in perf if r.get("kind") == "backend"]) == 1
        perf_end = [r for r in perf if r.get("kind") == "end"]
        debug_end = [r for r in debug if r.get("kind") == "end"]
        assert len(perf_end) == len(debug_end) == 1
        phases = [r for r in perf if r.get("kind") == "phase"]
        names = {r.get("phase") for r in phases}
        assert {"http.listen", "http.receive", "http.dispatch"} <= names, names
        assert all(isinstance(r.get("duration_ns"), int) and
                   r["duration_ns"] >= 0 for r in phases)
        assert perf_end[0]["recorded"] == len(phases)
        assert perf_end[0]["reserved"] >= len(phases)

        events = [r for r in debug if r.get("kind") == "event"]
        assert any(r.get("event") == "http.listener.ready" for r in events)
        assert debug_end[0]["recorded"] == len(events)
        assert all(isinstance(r.get("value"), int) for r in events)

        # Transport and handler-specific data must not enter fixed-name core
        # telemetry, which deliberately has no request-scoped identifier.
        core_text = "\n".join(json.dumps(r) for r in perf + debug)
        for sensitive in ("/foo/bar/baz", "first version", "created again",
                          "ores-trace-", "request_id"):
            assert sensitive not in core_text, sensitive

        roots = [r for r in otel if r.get("signal") == "span"
                 and r.get("name") == "http.server.request"]
        assert len(roots) >= 10
        assert all(r.get("duration_ns", -1) >= 0 for r in roots)
        assert len({r["ores_trace_id"] for r in roots}) >= 10
        assert len([r for r in otel if r.get("signal") == "metric"
                    and r.get("name") == "http.server.request.duration"]) >= 10
        print(f"PASS: 10 HTTP requests, {len(phases)} core phases, "
              f"{len(events)} core debug events and {len(roots)} userland request spans")


if __name__ == "__main__":
    main()
