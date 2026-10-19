#!/usr/bin/env python3
"""Structural preflight for the canonical +/ module-import demo.

This checks source layout and spellings. The compiler/HTTP integration suite
remains necessary for linked execution evidence.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / ".ores.pkg.toml"
assert PROJECT.is_file(), "missing canonical package manifest"
contents = PROJECT.read_text(encoding="utf-8")
assert 'schema_version = "1"' in contents
assert 'root = "."' in contents
assert (ROOT / "lib/store.ores").is_file()
assert (ROOT / "lib/telemetry.ores").is_file()
assert not (ROOT / "src/lib/store.ores").exists()
assert not (ROOT / "src/lib/telemetry.ores").exists()

IMPORT = re.compile(r'import\s+(?:module|fnc|actor|class)\s+\w+\s+from\s+"([^"]+)"\s*;')
seen_telemetry = seen_store = 0
for source in [ROOT / "src/main.ores", *(ROOT / "src/routes/rest").rglob("*.ores")]:
    raw = source.read_text(encoding="utf-8")
    matches = IMPORT.findall(raw)
    assert matches, f"{source}: expected import statement"
    for path in matches:
        assert not path.startswith("+") or path.startswith("+/"), (
            f"{source}: noncanonical project-root import {path!r}"
        )
        if path.startswith("+/"):
            assert "/../" not in path and "/./" not in path, (
                f"{source}: disallowed project-root import segment {path!r}"
            )
        if path.endswith("/telemetry"):
            assert path == "+/lib/telemetry", (source, path)
            seen_telemetry += 1
        if path.endswith("/store"):
            assert path == "+/lib/store", (source, path)
            seen_store += 1
assert seen_telemetry >= 7, seen_telemetry
assert seen_store == 5, seen_store
assert 'import module config from "+/src/generated/config";' in (
    ROOT / "lib/store.ores"
).read_text(encoding="utf-8")

ref = (ROOT / "SOURCE_REF").read_text(encoding="ascii").strip()
assert re.fullmatch(r"[0-9a-f]{40}", ref), "SOURCE_REF must pin an exact compiler commit"
print("project-root-import layout: OK (structural only; real compiler tests still required)")
