#!/usr/bin/env python3
"""Create the relocatable Oreslang guest source archive for native-image.

This source-free-on-host utility is also tested without GraalVM. The manifest
and all +/ import targets must be in the bundle that the native launcher unpacks.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

SOURCE_DIRECTORIES = (
    "src",
    "lib",
    "dependencies/otel/src",
    "dependencies/spin/src",
    "dependencies/spin/dependencies/http-routing/src",
)
MANIFEST = ".ores.pkg.toml"
CONFIG = "src/generated/config.ores"
REDACTED_CONFIG = b"define module config as\nend\n"


def checked_file(root: Path, path: Path) -> bytes:
    """Read a build input without accepting symlinks along its relative path."""
    relative = path.relative_to(root)
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f"Refusing symlinked embedded application input: {relative}")
    if not path.is_file():
        raise ValueError(f"Embedded application input is not a regular file: {relative}")
    return path.read_bytes()


def build_archive(root: Path, destination: Path) -> None:
    root = root.resolve(strict=True)
    manifest = root / MANIFEST
    if not manifest.is_file():
        raise ValueError(f"Missing required embedded project manifest: {manifest}")
    # Read and check all inputs before creating an archive so a malformed
    # source tree does not leave a partial output behind.
    sources: dict[str, bytes] = {MANIFEST: checked_file(root, manifest)}
    for directory_name in SOURCE_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir():
            raise ValueError(f"Missing embedded source directory: {directory_name}")
        for path in sorted(directory.rglob("*.ores")):
            name = path.relative_to(root).as_posix()
            if name in sources:
                raise ValueError(f"Duplicate embedded application path: {name}")
            body = checked_file(root, path)
            sources[name] = REDACTED_CONFIG if name == CONFIG else body
    for required in ("src/main.ores", CONFIG):
        if required not in sources:
            raise ValueError(f"Missing required native entry/config source: {required}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, body in sorted(sources.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, body)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: build_embedded_application.py REPO_ROOT ZIP_OUTPUT")
    build_archive(Path(sys.argv[1]), Path(sys.argv[2]))
