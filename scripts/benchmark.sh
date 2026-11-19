#!/usr/bin/env bash
# Destructive local-only REST benchmark. Implementation is Rust, not Python.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec cargo run --quiet --locked --manifest-path "$root/tools/profile/Cargo.toml" --bin benchmark -- "$@"
