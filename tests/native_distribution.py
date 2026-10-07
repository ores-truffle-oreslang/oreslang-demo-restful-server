#!/usr/bin/env python3
"""Validate a relocatable native archive, independent of the compiler checkout."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
archive = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='ores native relocation ') as tmp:
    root = Path(tmp)
    with tarfile.open(archive) as tar:
        # Python 3.9-compatible extraction; allow only internal files/directories
        # and license symlinks, with every resolved destination checked.
        for member in tar:
            target = root / member.name
            if not target.resolve().is_relative_to(root.resolve()):
                raise AssertionError("archive entry escapes extraction root")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.issym():
                link = (target.parent / member.linkname).resolve()
                if not link.is_relative_to(root.resolve()):
                    raise AssertionError("archive symlink escapes extraction root")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            else:
                raise AssertionError("unsupported archive entry")
    directories = [path for path in root.iterdir() if path.is_dir()]
    assert len(directories) == 1
    package = directories[0]
    binary = package / 'bin/rest-server'
    env = dict(os.environ, PATH='/nonexistent', DATA_DIR=str(root / 'data with "quotes"'))
    for name in ['JAVA_HOME', 'ORES_JAVA', 'ORESLANG_SOURCE_DIR', 'CLASSPATH']:
        env.pop(name, None)
    def run(*args):
        return subprocess.run([str(binary), *args], env=env, cwd=root,
                              text=True, capture_output=True, timeout=20)
    info = run('--build-info')
    assert info.returncode == 0, info.stderr
    mode = json.loads(info.stdout)['build_mode']
    expected = json.loads((package / 'build-info.json').read_text())['build_mode']
    assert mode == expected
    forged = run('-Dores.native.build-mode=' + ('hybrid' if mode == 'aot' else 'aot'), '--build-info')
    assert json.loads(forged.stdout)['build_mode'] == mode
    for bad in (['jit', 'hybrid'] if mode == 'aot' else ['jit']):
        rejected = run('--mode=' + bad)
        assert rejected.returncode != 0 and ('AOT-only' in rejected.stderr or 'Native hybrid' in rejected.stderr), rejected.stderr
    for port in ['0', '65536', '3;echo bad', '-1']:
        assert run('--port=' + port).returncode != 0
    import hashlib
    manifest = json.loads((package / 'manifest.json').read_text())
    for name, checksum in manifest['sha256'].items():
        assert hashlib.sha256((package / name).read_bytes()).hexdigest() == checksum, name
    # Use the same full HTTP/restart suite; only the test driver has Python/curl.
    subprocess.run([sys.executable, str(ROOT / 'tests/integration.py')], check=True,
                   env=dict(os.environ, REST_SERVER_BINARY=str(binary)), timeout=90)
    if mode == 'hybrid':
        subprocess.run([sys.executable, str(ROOT / 'tests/integration.py')], check=True,
                       env=dict(os.environ, REST_SERVER_BINARY=str(binary), REST_EXECUTION_MODE='aot'), timeout=90)
    libraries = list((package / 'lib').glob('liboresthread.*'))
    assert len(libraries) == 1
    missing = libraries[0].with_suffix('.missing')
    libraries[0].rename(missing)
    failed = run('--port=54329')
    assert failed.returncode != 0 and 'native Oreslang carrier backend was required' in failed.stderr, failed.stderr
    print(f'PASS: {mode} archive relocation, mode enforcement, checksums, missing-library failure and HTTP/telemetry/generator checks')
