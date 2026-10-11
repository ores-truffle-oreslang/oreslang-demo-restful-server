#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
expected="$(tr -d '[:space:]' < "$root/SOURCE_REF")"
export ORESLANG_SOURCE_DIR="${ORESLANG_SOURCE_DIR:-$root/.cache/compiler-$expected}"
if [[ -z "${ORES_JAVA:-}" && -n "${JAVA_HOME:-}" ]]; then
  export ORES_JAVA="$JAVA_HOME/bin/java"
fi
bash "$root/scripts/check-deps.sh" --all
python3 "$root/scripts/generate-routes.py" --port "${PORT:-3000}" --data-dir "${DATA_DIR:-$root/data}"
data_dir="$(cd "${DATA_DIR:-$root/data}" && pwd -P)"
cd "$root"
exec bash "$root/dependencies/spin/scripts/compiler.sh" --platform=server \
  --allow-net="127.0.0.1:${PORT:-3000}" --allow-read="$data_dir" --allow-write="$data_dir" "$@" "$root/src/main.ores"
