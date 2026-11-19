#!/usr/bin/env bash
# Build and run the Rust host route publisher. No Python interpreter required.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec cargo run --quiet --offline --locked --manifest-path "$root/tools/route-gen/Cargo.toml" -- "$@"
