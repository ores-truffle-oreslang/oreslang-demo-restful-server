#!/usr/bin/env bash
# Real Python generator admission checks; host-only, no AttoLang guest execution.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd -P)"
python3 - "$root" <<'PY'
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

path = Path(sys.argv[1]) / 'scripts/generate-routes.py'
spec = importlib.util.spec_from_file_location('rest_generator_trust_root', path)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)

def denied(f):
    try:
        f()
    except ValueError:
        return
    raise AssertionError('untrusted route tree unexpectedly admitted')

with TemporaryDirectory() as tmp:
    base = Path(tmp)
    real = base / 'real'
    (real / 'health').mkdir(parents=True)
    (real / 'health/get.ores').write_text('test', encoding='utf-8')
    assert [(m, s) for m, s, _ in generator.discover(real)] == [('GET', ('health',))]
    alias = base / 'route-alias'
    alias.symlink_to(real, target_is_directory=True)
    denied(lambda: generator.discover(alias))
    alias.unlink()
    for bad in ('a.b', 'a b', 'é', 'a+b'):
        nested = real / bad
        nested.mkdir()
        (nested / 'get.ores').write_text('test', encoding='utf-8')
        denied(lambda: generator.discover(real))
        (nested / 'get.ores').unlink()
        nested.rmdir()
    (real / 'health/GET.ores').write_text('test', encoding='utf-8')
    denied(lambda: generator.discover(real))
    (real / 'health/GET.ores').unlink()
    (real / 'health/get.ores').unlink()
    denied(lambda: generator.discover(real))
print('REST_DISCOVERY_TRUST_ROOT_HOST_PASS cases=8 guest=NOT_EXECUTED')
PY
