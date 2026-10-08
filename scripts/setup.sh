#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="${ORESLANG_SOURCE_DIR:-$root/.cache/oreslang-source.java}"
expected="$(tr -d '[:space:]' < "$root/SOURCE_REF")"
git -C "$root" submodule update --init --recursive
if [[ ! -d "$source_dir/.git" ]]; then
  mkdir -p "$(dirname "$source_dir")"
  git clone https://github.com/ores-truffle-oreslang/oreslang-source.java.git "$source_dir"
  git -C "$source_dir" checkout --detach "$expected"
fi
[[ "$(git -C "$source_dir" rev-parse HEAD)" == "$expected" ]] || { echo "Compiler must be at $expected; refusing to change an existing checkout." >&2; exit 65; }
[[ -z "$(git -C "$source_dir" status --porcelain)" ]] || { echo 'Compiler checkout must be clean.' >&2; exit 65; }
mvn -q -f "$source_dir/pom.xml" -DskipTests package dependency:build-classpath -Dmdep.outputFile=target/classpath.txt
echo 'Ready: ./scripts/run-server.sh (JDK 25 must also be used to run it).'
