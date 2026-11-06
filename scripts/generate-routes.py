#!/usr/bin/env python3
"""Generate a static Ores route table; never resolve request paths on disk."""
import argparse
import json
import re
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

def publish(path, text):
    # Replacing the directory entry avoids following a stale generated symlink
    # and keeps readers from observing a truncated generated source file.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".routes-", delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def discover(root):
    # The route root itself is a trust boundary. If it is a symlink, resolving
    # child files relative to root.resolve() would make an external tree appear
    # in-bounds. Refuse that shape before walking it.
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"route root must be a real directory: {root}")
    result = []
    root_resolved = root.resolve()
    # rglob("*.ores") skips symlinked directories entirely, hiding untrusted
    # route-tree aliases. Audit all entries before filtering by suffix.
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(root_resolved):
            raise ValueError(f"route must remain under route root: {path}")
        if path.suffix != ".ores":
            continue
        # rglob("*") can yield directories and special filesystem nodes;
        # only ordinary source files are valid compiler import targets.
        if not path.is_file():
            raise ValueError(f"route must be a regular source file: {path}")
        method = path.stem
        if method not in METHODS:
            raise ValueError(f"unsupported method filename: {path.name}")
        parts = path.relative_to(root).parts[:-1]
        if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", part) for part in parts):
            raise ValueError(f"only literal URL segments are supported: {path}")
        result.append((method.upper(), parts, path))
    if not result:
        raise ValueError("no REST routes found")
    return result

def generate(port, data_dir):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be in 1..65535")
    # A symlinked ancestor makes the entire source/publication tree external
    # even when the deeper routes/rest directory itself is not a symlink.
    source_root = ROOT / "src"
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError(f"source root must be a real directory: {source_root}")
    # The parent routes/ can itself point outside this source tree even when
    # its rest/ child is an ordinary directory. Reject that ancestor alias.
    route_tree_parent = source_root / "routes"
    if route_tree_parent.is_symlink() or not route_tree_parent.is_dir():
        raise ValueError(f"routes ancestor must be a real directory: {route_tree_parent}")
    routes = discover(source_root / "routes/rest")
    generated = source_root / "generated"
    if generated.is_symlink():
        raise ValueError("generated source directory must not be a symlink")
    # A missing/uncreatable data directory must not leave half-published sources.
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    generated.mkdir(parents=True, exist_ok=True)
    lines = ['import module spin from "../../dependencies/spin/src/spin";',
             'import module http_routing from "../../dependencies/spin/dependencies/http-routing/src/http_routing";']
    for index, (_, _, path) in enumerate(routes):
        relative = path.relative_to(ROOT / "src").with_suffix("").as_posix()
        lines.append(f'import module route as route_{index} from "../{relative}";')
    lines += ['', 'define module routes as', '  pub fnc register(borrow mut spin.Builder builder): void {']
    for index, (method, parts, _) in enumerate(routes):
        url = "/" + "/".join(parts)
        segments = ", ".join(f"http_routing.literal({json.dumps(part)})" for part in parts)
        lines.append(f'    val path_{index} = http_routing.path({json.dumps(url)}, arr[{segments}]);')
        lines.append(f'    builder.route("{method}", rt borrow path_{index}, route_{index}.handler(), 0, 0, "{method} {url}").unwrap();')
    lines += ['    return;', '  }', 'end', '']
    publish(generated / "routes.ores", "\n".join(lines))
    publish(generated / "config.ores",
        'define module config as\n'
        f'  pub fnc port(): int {{ return {port}; }}\n'
        f'  pub fnc data_path(): String {{ return {json.dumps(str(data_dir / "baz.txt"))}; }}\n'
        'end\n')
    print(f"Generated {len(routes)} method routes from src/routes/rest", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    generate(args.port, args.data_dir)
