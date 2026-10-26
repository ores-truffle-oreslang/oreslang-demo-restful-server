#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ORESLANG_SOURCE_DIR="${ORESLANG_SOURCE_DIR:-$root/.cache/oreslang-source.java}"
if [[ -z "${ORES_JAVA:-}" && -n "${JAVA_HOME:-}" ]]; then
  export ORES_JAVA="$JAVA_HOME/bin/java"
fi
if git -C "$root" submodule status --recursive | grep -Eq '^[+-U]'; then
  echo 'Run scripts/setup.sh to initialize the pinned dependencies.' >&2
  exit 65
fi
expected="$(tr -d '[:space:]' < "$root/SOURCE_REF")"
[[ "$(git -C "$ORESLANG_SOURCE_DIR" rev-parse HEAD)" == "$expected" ]] || { echo 'Run scripts/setup.sh to install the pinned compiler.' >&2; exit 65; }
[[ -z "$(git -C "$ORESLANG_SOURCE_DIR" status --porcelain)" ]] || { echo 'Compiler checkout must be clean.' >&2; exit 65; }

port="${PORT:-3000}"
if [[ ! "$port" =~ ^[0-9]{1,5}$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
  echo 'PORT must be a decimal integer in 1..65535.' >&2
  exit 64
fi

data_input="${DATA_DIR:-$root/data}"
[[ -n "$data_input" ]] || { echo 'DATA_DIR cannot be empty.' >&2; exit 64; }
mkdir -p -- "$data_input"
data_dir="$(cd -- "$data_input" && pwd -P)"
if [[ "$data_dir" == "/" ]]; then
  echo 'Refusing to grant demo filesystem capabilities to the filesystem root.' >&2
  exit 64
fi
if [[ "$data_dir" == *","* ]]; then
  echo 'DATA_DIR cannot contain a comma because runtime capability lists are comma-separated.' >&2
  exit 64
fi

python3 "$root/scripts/generate-routes.py" --port "$port" --data-dir "$data_dir"
cd "$root"
exec bash "$root/dependencies/spin/scripts/compiler.sh" --platform=server \
  --allow-net="127.0.0.1:$port" --allow-read="$data_dir" --allow-write="$data_dir" "$@" "$root/src/main.ores"
