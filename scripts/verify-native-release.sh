#!/usr/bin/env bash
# Real REST server delivery gate: compile the complete pinned GraalVM native
# application and execute the Rust HTTP/telemetry/shutdown contracts.
# Never report full guest success without a native executable.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
mode="${1:-aot}"
if [[ $# -gt 1 ]]; then
  echo 'Usage: bash scripts/verify-native-release.sh [aot|hybrid]' >&2
  exit 64
fi
case "$mode" in
  aot|hybrid) ;;
  *) echo 'Mode must be aot or hybrid.' >&2; exit 64 ;;
esac
: "${JAVA_HOME:?Set JAVA_HOME to a compatible GraalVM native-image SDK}"
: "${ORESLANG_SOURCE_DIR:?Set ORESLANG_SOURCE_DIR to the pinned private compiler checkout}"
[[ -x "$JAVA_HOME/bin/native-image" ]] || {
  echo 'Full-app native verification requires a real GraalVM native-image binary' >&2
  exit 69
}
[[ -f "$ORESLANG_SOURCE_DIR/pom.xml" ]] || {
  echo 'Full-app native verification requires the exact Java compiler checkout' >&2
  exit 69
}
bash "$root/scripts/build-native.sh" "$mode"
platform="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
dist="$root/dist/rest-server-$mode-$platform"
server="$dist/bin/rest-server"
[[ -x "$server" && -s "$dist/manifest.json" && -s "$dist/build-info.json" ]] || {
  echo 'Native distribution is incomplete: refusing to report a passing REST app' >&2
  exit 1
}
app_head="$(git -C "$root" rev-parse HEAD)"
grep -Fq "$app_head" "$dist/manifest.json" || {
  echo 'Native manifest does not name the exact source head' >&2
  exit 1
}
bash "$root/scripts/integration-rust.sh" "$server"
printf 'REST_NATIVE_FULL_GUEST_PASS mode=%s source_sha=%s\n' "$mode" "$app_head"
