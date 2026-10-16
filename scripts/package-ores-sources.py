#!/usr/bin/env python3
"""Deterministically bundle Oreslang sources and the canonical package manifest.

Native Image unpacks this archive before resolving +/ imports. A missing
manifest or a traversed symlink must never silently change package identity.
"""
import argparse
import os
from pathlib import Path
import stat
import tempfile
import zipfile

SOURCE_DIRS = (
    "src",
    "dependencies/otel/src",
    "dependencies/spin/src",
    "dependencies/spin/dependencies/http-routing/src",
)
MAX_FILES = 8192
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
CONFIG_PLACEHOLDER = b"define module config as\nend\n"


def source_entries(root):
    root = Path(root).resolve(strict=True)
    manifest = root / ".ores.pkg.toml"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("missing, invalid, or symlinked package manifest .ores.pkg.toml")
    files = [manifest]
    directories_seen = 0
    for relative_dir in SOURCE_DIRS:
        directory = root / relative_dir
        if directory.is_symlink():
            raise ValueError("symlinked source directory: " + relative_dir)
        if not directory.is_dir():
            raise ValueError("missing source directory: " + relative_dir)
        for current, dirs, names in os.walk(directory, followlinks=False):
            directories_seen += 1
            if directories_seen > 2048:
                raise ValueError("too many source directories")
            current = Path(current)
            if len(current.relative_to(directory).parts) > 64:
                raise ValueError("source directory nesting exceeds 64")
            for name in dirs:
                child = current / name
                if child.is_symlink():
                    raise ValueError("symlinked source directory: " + str(child))
            for name in names:
                if not name.endswith(".ores"):
                    continue
                path = current / name
                if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError("symlinked or non-regular Oreslang source: " + str(path))
                files.append(path)
                if len(files) > MAX_FILES:
                    raise ValueError("too many Oreslang source files")
    # No duplicate ZIP paths, even if source roots overlap after refactors.
    entries = {}
    total = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        if relative in entries:
            raise ValueError("duplicate archive path: " + relative)
        data = CONFIG_PLACEHOLDER if relative == "src/generated/config.ores" else path.read_bytes()
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("source file exceeds bundle limit: " + relative)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("source bundle exceeds byte limit")
        entries[relative] = data
    return sorted(entries.items())


def pack(root, output):
    output = Path(output)
    entries = source_entries(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".ores-app-",
                                         suffix=".zip", delete=False) as tmp:
            temporary = Path(tmp.name)
        with zipfile.ZipFile(temporary, "w") as archive:
            for name, data in entries:
                info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print("Packed", pack(args.root, args.output), "package sources into", args.output)
