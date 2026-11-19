#!/usr/bin/env bash
# Rust HTTP/actor-header integration driver for an *actual* standalone native REST binary.
# This does not pretend a missing compiler/guest is a passed integration test.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 1 || -z "$1" ]]; then
  echo 'Usage: bash scripts/integration-rust.sh /absolute/path/to/native/rest-server' >&2
  exit 64
fi
exec cargo run --quiet --locked --manifest-path "$root/tools/profile/Cargo.toml" --bin integration -- --server "$1"
