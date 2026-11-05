#!/usr/bin/env bash
# Host-only filesystem test for the real generator's atomic publish primitive.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd -P)"
python3 - "$root" <<'PY'
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

path = Path(sys.argv[1]) / 'scripts/generate-routes.py'
spec = importlib.util.spec_from_file_location('rest_generator_publication', path)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)

with TemporaryDirectory() as temp:
    root = Path(temp)
    generated = root / 'generated'
    generated.mkdir()
    outside = root / 'outside-secret'
    outside.write_text('keep-secret\n', encoding='utf-8')
    output = generated / 'routes.ores'
    output.symlink_to(outside)
    assert output.is_symlink()
    generator.publish(output, 'new-routes\n')
    assert output.is_file() and not output.is_symlink()
    assert output.read_text(encoding='utf-8') == 'new-routes\n'
    assert outside.read_text(encoding='utf-8') == 'keep-secret\n'
    assert not list(generated.glob('.routes-*')), 'stale temporary publication'
    generator.publish(output, 'replacement\n')
    assert output.read_text(encoding='utf-8') == 'replacement\n'
    assert outside.read_text(encoding='utf-8') == 'keep-secret\n'
    assert not list(generated.glob('.routes-*'))
print('REST_ATOMIC_PUBLICATION_HOST_PASS cases=7 guest=NOT_EXECUTED')
PY
