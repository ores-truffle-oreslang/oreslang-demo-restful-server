#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ORESLANG_SOURCE_DIR="${ORESLANG_SOURCE_DIR:-$root/.cache/oreslang-source.java}"
for script in setup.sh run-server.sh curl-10.sh; do
  bash -n "$root/scripts/$script"
done
python3 "$root/tests/integration.py"
