#!/usr/bin/env python3
"""Deterministically bundle Oreslang sources and the canonical package manifest.

Native Image unpacks this archive before resolving +/ imports. A missing
manifest or a traversed symlink must never silently change package identity.
"""
import argparse
import os
import posixpath
import re
from pathlib import PurePosixPath
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


def read_bounded_regular_file(path, label):
    """Open without following a final symlink and bound file contents."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("non-regular bundle input: " + label)
        # Hardlinks can smuggle otherwise untracked outside-checkout data
        # into the bundle, even without using symbolic links.
        if metadata.st_nlink != 1:
            raise ValueError("hardlinked bundle input: " + label)
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("source file exceeds bundle limit: " + label)
    return data


def source_entries(root):
    root = Path(root).resolve(strict=True)
    manifest = root / ".ores.pkg.toml"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("missing, invalid, or symlinked package manifest .ores.pkg.toml")
    files = [manifest]
    directories_seen = 0
    for relative_dir in SOURCE_DIRS:
        # A symlink in an ancestor is just as dangerous as one at the
        # directory leaf: dependencies/spin -> /outside would otherwise
        # escape the trusted checkout via dependencies/spin/src.
        parts = Path(relative_dir).parts
        for depth in range(1, len(parts) + 1):
            ancestor = root.joinpath(*parts[:depth])
            if ancestor.is_symlink():
                raise ValueError("symlinked source directory: " + str(ancestor))
        directory = root / relative_dir
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
        raw_bytes = read_bounded_regular_file(path, relative)
        data = CONFIG_PLACEHOLDER if relative == "src/generated/config.ores" else raw_bytes
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("source file exceeds bundle limit: " + relative)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("source bundle exceeds byte limit")
        entries[relative] = data
    # Inspect source imports in the artifact being published. This checks
    # demo-style single-line imports, not arbitrary Oreslang syntax; the full
    # compiler still owns authoritative parsing and linking.
    import_pattern = re.compile(
        rb'(?m)^[ \t]*import\b[^\r\n;]*\bfrom[ \t]+"([^"\r\n]*)"[ \t]*;'
    )
    names = set(entries)
    if "src/main.ores" not in names:
        raise ValueError("native application entrypoint missing from bundle: src/main.ores")
    for origin, data in entries.items():
        if not origin.endswith(".ores"):
            continue
        # Don't mistake a missed/multiline import for a successfully checked
        # dependency. Native bundling supports only single-line declarations,
        # so reject unsupported spellings instead of silently omitting them.
        for line in data.splitlines():
            if re.match(rb"^[ \t]*import\b", line) and not import_pattern.match(line):
                raise ValueError("unsupported import declaration in native bundle: " + origin)
        for match in import_pattern.finditer(data):
            import_path = match.group(1).decode("utf-8")
            if import_path.startswith("+"):
                if not import_path.startswith("+/"):
                    raise ValueError("malformed +/ import in bundle: " + origin)
                raw = import_path[2:].replace("\\", "/")
                if not raw or raw.startswith("/") or ".." in raw.split("/"):
                    raise ValueError("rooted import escapes or lacks package-relative path: " + origin)
                target = PurePosixPath(raw).as_posix()
                kind = "rooted"
            elif import_path.startswith("./") or import_path.startswith("../"):
                raw = import_path.replace("\\", "/")
                target = posixpath.normpath(posixpath.join(posixpath.dirname(origin), raw))
                if target == ".." or target.startswith("../") or target.startswith("/"):
                    raise ValueError("relative import escapes source bundle: " + origin + " -> " + import_path)
                kind = "relative"
            elif import_path.startswith("@"):
                raise ValueError("retired @/ import in source bundle: " + origin)
            elif (import_path.startswith("/") or import_path.startswith("\\\\")
                  or re.match(r"^[A-Za-z]:[/\\\\]", import_path)
                  or import_path.startswith("file:")):
                raise ValueError("absolute filesystem import cannot be bundled: " + origin + " -> " + import_path)
            elif import_path.startswith("pkg:"):
                # No zed_modules or verified lockfile is shipped in this demo ZIP.
                raise ValueError("unbundled pkg: import is unsupported in native demo: "
                                 + origin + " -> " + import_path)
            else:
                # Bare and java: imports have their own compiler namespaces.
                continue
            if target not in names and (target + ".ores") not in names:
                raise ValueError(kind + " import target missing from bundle: " + origin + " -> " + import_path)
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
