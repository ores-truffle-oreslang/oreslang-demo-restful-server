#!/usr/bin/env python3
"""Generate a static Ores route table; never resolve request paths on disk."""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

def discover(root):
    result = []
    for path in sorted(root.rglob("*.ores")):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"route must remain under route root: {path}")
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
    if not 1 <= port <= 65535:
        raise ValueError("port must be in 1..65535")
    routes = discover(ROOT / "src/routes/rest")
    generated = ROOT / "src/generated"
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
    (generated / "routes.ores").write_text("\n".join(lines))
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (generated / "config.ores").write_text(
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
