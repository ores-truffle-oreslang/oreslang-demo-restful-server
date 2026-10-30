#!/usr/bin/env bash
# Fail-closed tracked-source policy; host-only, no Oreslang guest execution.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
root="${SOURCE_POLICY_ROOT:-$repo_root}"
root="$(cd "$root" && pwd -P)"
cd "$root"
[[ "$(git rev-parse --show-toplevel)" == "$root" ]] || { echo "POLICY_FAILURE: Git repository root mismatch" >&2; exit 2; }
if [[ -n "${EXPECTED_DEMO_SHA:-}" ]]; then
  [[ "${EXPECTED_DEMO_SHA}" =~ ^[a-f0-9]{40}$ ]] || { echo "POLICY_FAILURE: invalid expected head" >&2; exit 2; }
  [[ "$(git rev-parse HEAD)" == "$EXPECTED_DEMO_SHA" ]] || { echo "POLICY_FAILURE: mismatched head" >&2; exit 2; }
fi
if [[ -f SOURCE_REF ]]; then
  [[ "$(wc -l < SOURCE_REF)" -eq 1 ]] && grep -Eq '^[a-f0-9]{40}$' SOURCE_REF && [[ "$(wc -c < SOURCE_REF)" -eq 41 ]] || {
    echo "POLICY_FAILURE: SOURCE_REF requires exactly 40 lowercase hex characters and newline" >&2; exit 2;
  }
fi
mode="${SOURCE_POLICY_MODE:-strict}"
[[ "$mode" == strict || "$mode" == rest-legacy ]] || { echo "POLICY_FAILURE: unknown mode" >&2; exit 2; }
count=0
while IFS= read -r -d '' path; do
  count=$((count+1))
  (( count <= 50000 )) || { echo "POLICY_FAILURE: file budget" >&2; exit 2; }
  if [[ -L "$path" ]]; then
    case "${path,,}" in
      *.ores|*.py|*.pyi|*.ipynb)
        echo "POLICY_FAILURE: symlinked source: $path" >&2; exit 2 ;;
    esac
  fi
  allowed=0
  if [[ "$mode" == rest-legacy ]]; then
    case "$path" in
      scripts/benchmark.py|scripts/generate-routes.py|tests/core_diagnostics.py|tests/integration.py|tests/native_distribution.py|tests/test_route_publication.py)
        allowed=1
        printf 'LEGACY_PYTHON_NONCOMPLIANT existing_path=%s\n' "$path"
        ;;
    esac
  fi
  case "${path,,}" in
    *.py|*.pyi|*.ipynb)
      if (( ! allowed )); then
        printf 'POLICY_FAILURE: Python source is not permitted: %s\n' "$path" >&2
        exit 2
      fi
      ;;
  esac
  if (( ! allowed )) && [[ -f "$path" ]]; then
    IFS= read -r firstline < "$path" || true
    if [[ "$firstline" =~ ^\#!.*(^|[/[:space:]])(python|pypy)([0-9.]*)?([[:space:]]|$) ]]; then
      printf 'POLICY_FAILURE: Python interpreter shebang: %s\n' "$path" >&2
      exit 2
    fi
  fi
done < <(git ls-files -z --cached)
printf 'SOURCE_POLICY_STRUCTURAL_PASS tracked_files=%s mode=%s\n' "$count" "$mode"
if [[ "$mode" == rest-legacy ]]; then
  echo 'LEGACY_PYTHON_NONCOMPLIANT: complete Rust replacement remains required'
fi
echo 'NOT_EXECUTED: this gate did not invoke an Oreslang compiler or guest'
