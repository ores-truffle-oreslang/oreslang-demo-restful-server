#!/usr/bin/env python3
"""Offline behavioral checks for fail-closed dependency validation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1]


def git(directory, *args):
    return subprocess.check_output(['git', '-C', str(directory), *args], text=True).strip()


def init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', str(path)], check=True, capture_output=True)
    git(path, 'config', 'user.email', 'ci@example.invalid')
    git(path, 'config', 'user.name', 'Offline Test')
    (path / 'payload.txt').write_text('original\n')
    git(path, 'add', 'payload.txt')
    git(path, 'commit', '-qm', 'initial')
    return git(path, 'rev-parse', 'HEAD')


class DependencyCheckerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'demo'
        (self.root / 'scripts').mkdir(parents=True)
        shutil.copy2(SOURCE_ROOT / 'scripts/check-deps.sh', self.root / 'scripts/check-deps.sh')
        self.compiler = Path(self.tmp.name) / 'compiler'
        self.sha = init_repo(self.compiler)
        (self.root / 'SOURCE_REF').write_text(self.sha + '\n')

    def check(self, *args, compiler=None, env=None):
        environ = dict(os.environ, ORESLANG_SOURCE_DIR=str(compiler or self.compiler))
        if env:
            environ.update(env)
        return subprocess.run(['bash', str(self.root / 'scripts/check-deps.sh'), *args],
                              capture_output=True, text=True, env=environ)

    def test_accepts_exact_clean_checkout(self):
        self.assertEqual(self.check('--compiler').returncode, 0)

    def test_rejects_wrong_pin(self):
        (self.root / 'SOURCE_REF').write_text('0' * 40 + '\n')
        self.assertNotEqual(self.check('--compiler').returncode, 0)

    def test_rejects_untrusted_pin_format(self):
        (self.root / 'SOURCE_REF').write_text(self.sha + '\ntrailing-data\n')
        self.assertNotEqual(self.check('--compiler').returncode, 0)

    def test_rejects_modified_and_untracked_compiler_files(self):
        (self.compiler / 'payload.txt').write_text('modified\n')
        self.assertNotEqual(self.check('--compiler').returncode, 0)
        (self.compiler / 'payload.txt').write_text('original\n')
        (self.compiler / 'evil.txt').write_text('unexpected\n')
        self.assertNotEqual(self.check('--compiler').returncode, 0)

    def test_rejects_missing_and_nonrepo_checkout(self):
        self.assertNotEqual(self.check('--compiler', compiler=self.root / 'missing').returncode, 0)
        fake = self.root / 'not-git'
        fake.mkdir()
        self.assertNotEqual(self.check('--compiler', compiler=fake).returncode, 0)

    def test_rejects_failed_git_inspection(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        mock_git = bin_dir / 'git'
        mock_git.write_text('#!/bin/sh\nexit 42\n')
        mock_git.chmod(0o755)
        result = self.check('--compiler', env={'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']})
        self.assertNotEqual(result.returncode, 0)

    def make_submodules(self):
        spin = Path(self.tmp.name) / 'spin-upstream'
        otel = Path(self.tmp.name) / 'otel-upstream'
        routing = Path(self.tmp.name) / 'routing-upstream'
        for path in (spin, otel, routing):
            init_repo(path)
        subprocess.run(['git', '-c', 'protocol.file.allow=always', '-C', str(spin),
                        'submodule', 'add', '-q', str(routing), 'dependencies/http-routing'],
                       check=True, capture_output=True)
        git(spin, 'commit', '-qam', 'pin nested router')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        git(self.root, 'config', 'user.email', 'ci@example.invalid')
        git(self.root, 'config', 'user.name', 'Offline Test')
        for source, dest in ((spin, 'dependencies/spin'), (otel, 'dependencies/otel')):
            subprocess.run(['git', '-c', 'protocol.file.allow=always', '-C', str(self.root),
                            'submodule', 'add', '-q', str(source), dest],
                           check=True, capture_output=True)
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'pin demo dependencies')
        subprocess.run(['git', '-c', 'protocol.file.allow=always', '-C', str(self.root),
                        'submodule', 'update', '--init', '--recursive'],
                       check=True, capture_output=True)

    def test_checks_clean_recursive_gitlinks(self):
        self.make_submodules()
        self.assertEqual(self.check('--all').returncode, 0)

    def test_rejects_dirty_nested_dependency_and_untracked_file(self):
        self.make_submodules()
        router = self.root / 'dependencies/spin/dependencies/http-routing/payload.txt'
        router.write_text('changed\n')
        self.assertNotEqual(self.check('--all').returncode, 0)
        router.write_text('original\n')
        (self.root / 'dependencies/otel/unknown.txt').write_text('unexpected\n')
        self.assertNotEqual(self.check('--all').returncode, 0)

    def test_rejects_dirty_gitmodules_and_gitlink(self):
        self.make_submodules()
        with (self.root / '.gitmodules').open('a') as stream:
            stream.write('\n# tampered\n')
        self.assertNotEqual(self.check('--all').returncode, 0)
        git(self.root, 'checkout', '--', '.gitmodules')
        spin = self.root / 'dependencies/spin'
        (spin / 'payload.txt').write_text('new commit\n')
        git(spin, 'add', 'payload.txt')
        git(spin, '-c', 'user.email=ci@example.invalid',
            '-c', 'user.name=Offline Test', 'commit', '-qm', 'unexpected')
        self.assertNotEqual(self.check('--all').returncode, 0)

    def test_setup_rejects_invalid_pin_before_fetching(self):
        shutil.copy2(SOURCE_ROOT / 'scripts/setup.sh', self.root / 'scripts/setup.sh')
        (self.root / 'SOURCE_REF').write_text('malformed\n')
        marker = self.root / 'git-called'
        fakebin = self.root / 'fakebin'
        fakebin.mkdir()
        fakegit = fakebin / 'git'
        fakegit.write_text('#!/bin/sh\necho called >> "' + str(marker) + '"\nexit 88\n')
        fakegit.chmod(0o755)
        env = dict(os.environ, PATH=str(fakebin) + os.pathsep + os.environ['PATH'],
                   ORESLANG_SOURCE_DIR=str(self.compiler))
        proc = subprocess.run(['bash', str(self.root / 'scripts/setup.sh')],
                              env=env, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(marker.exists(), 'Untrusted pin triggered Git before validation')

    def test_setup_rejects_dirty_gitmodules_before_submodule_update(self):
        shutil.copy2(SOURCE_ROOT / 'scripts/setup.sh', self.root / 'scripts/setup.sh')
        self.make_submodules()
        with (self.root / '.gitmodules').open('a') as stream:
            stream.write('\n# unreviewed update URL\n')
        output = subprocess.run(['bash', str(self.root / 'scripts/setup.sh')],
                                env=dict(os.environ, ORESLANG_SOURCE_DIR=str(self.compiler)),
                                capture_output=True, text=True)
        self.assertNotEqual(output.returncode, 0)
        self.assertIn('refusing dependency fetch', output.stderr)

    def test_rejects_absent_submodules(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.assertNotEqual(self.check('--all').returncode, 0)


if __name__ == '__main__':
    unittest.main()
