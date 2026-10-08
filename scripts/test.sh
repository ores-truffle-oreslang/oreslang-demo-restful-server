#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ORESLANG_SOURCE_DIR="${ORESLANG_SOURCE_DIR:-$root/.cache/oreslang-source.java}"
bash -n "$root/scripts/setup.sh" "$root/scripts/run-server.sh" "$root/scripts/curl-10.sh"
python3 "$root/tests/integration.py"
# Requires the diagnostic-aware compiler pinned in SOURCE_REF.
python3 "$root/tests/core_diagnostics.py"
