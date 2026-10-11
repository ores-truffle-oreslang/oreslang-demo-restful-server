#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Validate the pin and tracked remote declarations BEFORE any submodule fetch
# or compiler clone. A tampered .gitmodules must not select fetch endpoints.
expected="$(cat "$root/SOURCE_REF")"
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid SOURCE_REF; refusing dependency fetch.' >&2; exit 65; }
git -C "$root" diff --quiet HEAD -- .gitmodules dependencies/spin dependencies/otel \
  || { echo 'Submodule metadata/gitlinks modified; refusing dependency fetch.' >&2; exit 65; }
# A per-SHA default leaves an earlier pinned compiler checkout undisturbed.
source_dir="${ORESLANG_SOURCE_DIR:-$root/.cache/compiler-$expected}"
git -C "$root" submodule update --init --recursive
if [[ ! -e "$source_dir" && ! -L "$source_dir" ]]; then
  mkdir -p "$(dirname "$source_dir")"
  git clone https://github.com/ores-truffle-oreslang/oreslang-source.java.git "$source_dir"
  git -C "$source_dir" checkout --detach "$expected"
fi
bash "$root/scripts/check-deps.sh" --all
mvn -q -f "$source_dir/pom.xml" -DskipTests package dependency:build-classpath -Dmdep.outputFile=target/classpath.txt
echo 'Ready: ./scripts/run-server.sh (JDK 25 must also be used to run it).'
