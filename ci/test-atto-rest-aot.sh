#!/usr/bin/env bash
# Compile and execute a *real* AttoLang native scalar canary. No success without a compiler.
# Usage: bash ci/test-atto-rest-aot.sh rs /absolute/path/to/oresc
#        bash ci/test-atto-rest-aot.sh llvm /absolute/path/to/oreslang-llvmc
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# == 2 ]] || { echo 'Usage: test-atto-rest-aot.sh rs|llvm COMPILER' >&2; exit 64; }
mode="$1" compiler="$2"
[[ "$mode" == rs || "$mode" == llvm ]] || { echo 'Unsupported backend' >&2; exit 64; }
[[ -x "$compiler" ]] || { echo 'Native compiler binary is missing or not executable' >&2; exit 69; }
compiler="$(realpath "$compiler")"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
source="$root/tests/guest/rest_status_scalar.atto"
unsupported="$root/tests/guest/rest_http_actor_unimplemented.atto"
if [[ "$mode" == rs ]]; then
  "$compiler" --strict-aot "$source" "$tmp/guest.o"
  [[ -s "$tmp/guest.o" ]] || { echo 'Rust backend produced no native object' >&2; exit 1; }
  cc "$tmp/guest.o" -o "$tmp/guest"
  # A crash, timeout, or invocation failure must never count as proper rejection.
  set +e
  timeout --signal=TERM --kill-after=5s 30s "$compiler" --strict-aot "$unsupported" "$tmp/rejected.o" >"$tmp/negative.out" 2>"$tmp/negative.err"
  rejection_status=$?
  set -e
  [[ "$rejection_status" == 1 ]] || { echo "FAIL: expected semantic compiler exit 1, got $rejection_status" >&2; exit 1; }
  grep -Fq 'oresc:' "$tmp/negative.err" || { echo 'FAIL: missing canonical Rust compiler diagnostic' >&2; exit 1; }
  [[ ! -s "$tmp/negative.out" ]] || { echo 'FAIL: rejected Rust guest emitted stdout' >&2; exit 1; }
  [[ ! -e "$tmp/rejected.o" ]] || { echo 'FAIL: Rust backend emitted invalid HTTP object' >&2; exit 1; }
else
  "$compiler" "$source" > "$tmp/guest.ll"
  grep -Fq 'define ' "$tmp/guest.ll" || { echo 'LLVM backend did not emit LLVM IR' >&2; exit 1; }
  clang -x ir "$tmp/guest.ll" -o "$tmp/guest"
  set +e
  timeout --signal=TERM --kill-after=5s 30s "$compiler" "$unsupported" > "$tmp/rejected.ll" 2>"$tmp/negative.err"
  rejection_status=$?
  set -e
  [[ "$rejection_status" == 1 ]] || { echo "FAIL: expected LLVM semantic compiler exit 1, got $rejection_status" >&2; exit 1; }
  grep -Fq 'oreslang-llvmc:' "$tmp/negative.err" || { echo 'FAIL: missing canonical LLVM compiler diagnostic' >&2; exit 1; }
  if grep -Fq 'define ' "$tmp/rejected.ll"; then
    echo 'FAIL: LLVM backend emitted executable IR for unsupported guest' >&2
    exit 1
  fi
fi
set +e
"$tmp/guest"
exit_code=$?
set -e
[[ "$exit_code" == 42 ]] || { echo "FAIL: $mode guest native exit was $exit_code (expected 42)" >&2; exit 1; }
echo "ATTO_NATIVE_AOT_SCALAR_PASS backend=$mode exit=42"
echo 'HTTP_ACTOR_SOURCE_REJECTED: full AttoLang REST server NOT_EXECUTED'
