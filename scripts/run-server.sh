#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/scripts/lib/guards.sh"
ores_verify_submodule_contract "$root"
expected="$(ores_read_exact_source_ref "$root")"
export ORESLANG_SOURCE_DIR="${ORESLANG_SOURCE_DIR:-$root/.cache/compiler-$expected}"
if [[ -z "${ORES_JAVA:-}" && -n "${JAVA_HOME:-}" ]]; then
  export ORES_JAVA="$JAVA_HOME/bin/java"
fi
bash "$root/scripts/check-deps.sh" --all

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
