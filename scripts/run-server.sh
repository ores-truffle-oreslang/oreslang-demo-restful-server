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
python3 "$root/scripts/generate-routes.py" --port "${PORT:-3000}" --data-dir "${DATA_DIR:-$root/data}"
data_dir="$(cd "${DATA_DIR:-$root/data}" && pwd -P)"
cd "$root"
exec bash "$root/dependencies/spin/scripts/compiler.sh" --platform=server \
  --allow-net="127.0.0.1:${PORT:-3000}" --allow-read="$data_dir" --allow-write="$data_dir" "$@" "$root/src/main.ores"
