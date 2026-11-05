#!/usr/bin/env bash
# Source-free route discovery negative controls; does not run Oreslang guest.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
python3 - "$root" <<'PY'
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

src = Path(sys.argv[1]) / 'scripts/generate-routes.py'
spec = importlib.util.spec_from_file_location('route_generator', src)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

with TemporaryDirectory() as tmp:
    original_root = g.ROOT
    g.ROOT = Path(tmp)
    try:
        routes = g.ROOT / 'src/routes/rest'
        (routes / 'safe-endpoint').mkdir(parents=True)
        (routes / 'safe-endpoint/get.ores').write_text('fixture', encoding='utf-8')
        assert [r[:2] for r in g.discover(routes)] == [('GET', ('safe-endpoint',))]
        for invalid in ('trace.ores', 'bad.ores'):
            p = routes / 'safe-endpoint' / invalid
            p.write_text('not an admitted HTTP method', encoding='utf-8')
            try:
                g.discover(routes)
            except ValueError:
                pass
            else:
                raise AssertionError('unsupported method admitted')
            p.unlink()
        outside = g.ROOT / 'outside.ores'
        outside.write_text('secret', encoding='utf-8')
        alias = routes / 'safe-endpoint/post.ores'
        alias.symlink_to(outside)
        try:
            g.discover(routes)
        except ValueError:
            pass
        else:
            raise AssertionError('symlinked route admitted')
        alias.unlink()
        for port in (0, -1, 65536):
            try:
                g.generate(port, g.ROOT / 'data')
            except ValueError:
                pass
            else:
                raise AssertionError('out-of-range port admitted')
        assert not (g.ROOT / 'src/generated/routes.ores').exists()
    finally:
        g.ROOT = original_root
print('ROUTE_GENERATOR_NEGATIVE_HOST_PASS cases=8 guest=NOT_EXECUTED')
PY
