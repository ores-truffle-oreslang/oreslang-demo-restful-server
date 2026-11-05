#!/usr/bin/env bash
# Host-only route generator/source publication checks; no guest execution.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd -P)"
python3 - "$repo" <<'PY'
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

repo = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "generator_wave31", repo / "scripts/generate-routes.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    route_dir = root / "src/routes/rest"
    (route_dir / "team_alpha").mkdir(parents=True)
    (route_dir / "team_alpha/get.ores").write_text("fixture\n")
    (route_dir / "api/v1_2").mkdir(parents=True)
    (route_dir / "api/v1_2/post.ores").write_text("fixture\n")
    # Hostile shell characters must remain literal JSON string data.
    data = root / 'quoted"directory\\name\nnext'
    with patch.object(generator, "ROOT", root):
        generator.generate(65535, data)
        output = root / "src/generated"
        routes = (output / "routes.ores").read_text()
        config = (output / "config.ores").read_text()
        assert 'GET /team_alpha' in routes
        assert 'POST /api/v1_2' in routes
        assert 'http_routing.literal("v1_2")' in routes
        assert 'return 65535;' in config
        assert json.dumps(str(data.resolve() / "baz.txt")) in config
        original = {p.name: p.read_bytes() for p in output.glob("*.ores")}

        # Invalid later route discovery must not clobber earlier publications.
        bad = route_dir / "encoded%2fseparator"
        bad.mkdir()
        (bad / "get.ores").write_text("fixture")
        try:
            generator.generate(65535, root / "other-data")
        except ValueError as error:
            assert "literal URL segments" in str(error)
        else:
            raise AssertionError("encoded segment accepted")
        for name, before in original.items():
            assert (output / name).read_bytes() == before
        assert not (root / "other-data").exists()

        # Replaced output files are new regular files with prescribed mode.
        assert all((output / name).stat().st_mode & 0o777 == 0o644
                   for name in original)
print("HOST_ONLY_ROUTE_PUBLICATION_PASS cases=3; NOT_GUEST_EXECUTION")
PY
