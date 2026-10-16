#!/usr/bin/env python3
"""Source-only regression checks for canonical +/ Oreslang imports (Python 3.9+)."""
import importlib.util
import re
import shutil
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
manifest = ROOT / ".ores.pkg.toml"
assert manifest.is_file(), "package manifest missing"
assert 'schema_version = "1"' in manifest.read_text()
assert 'main = "src/main.ores"' in manifest.read_text()

routes = sorted((ROOT / "src/routes/rest").rglob("*.ores"))
assert len(routes) == 6, "expected six method files in the demo"
for source in [ROOT / "src/main.ores", *routes]:
    body = source.read_text()
    for imported in re.findall(r'from "([^"]+)"', body):
        if imported.startswith("+"):
            assert imported.startswith("+/"), (source, imported)
            rel = imported[2:]
            assert rel and not rel.startswith("/") and ".." not in Path(rel).parts
            assert (ROOT / (rel + ".ores")).is_file(), (source, imported)
    if source in routes:
        assert 'from "+/src/lib/telemetry"' in body
        if "/foo/bar/baz/" in source.as_posix():
            assert 'from "+/src/lib/store"' in body

spec = importlib.util.spec_from_file_location("route_generator", ROOT / "scripts/generate-routes.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
with tempfile.TemporaryDirectory() as tmp:
    temp_root = Path(tmp)
    shutil.copytree(ROOT / "src/routes", temp_root / "src/routes")
    generator.ROOT = temp_root
    generator.generate(3000, temp_root / "data")
    generated = (temp_root / "src/generated/routes.ores").read_text()
    assert generated.count('import module route as route_') == 6
    for source in routes:
        rel = source.relative_to(ROOT).with_suffix("").as_posix()
        assert f'from "+/{rel}"' in generated, rel
    assert 'from "../routes/' not in generated

print("PASS: canonical +/ imports, resolved project files, generated route imports")
