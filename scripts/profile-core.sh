#!/usr/bin/env bash
# Six rounds per mode, one warm-up round excluded; validated by the Rust runner.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec cargo run --quiet --locked --manifest-path "$root/tools/profile/Cargo.toml"
