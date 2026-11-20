#!/usr/bin/env bash
# Verify the exact compiler and recursive dependency graph before execution.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
mode="${1:---all}"
[[ "$mode" == --all || "$mode" == --compiler ]] || { echo 'Usage: check-deps.sh [--all|--compiler]' >&2; exit 64; }
die() { echo "Dependency verification failed: $*" >&2; exit 65; }

expected="$(cat "$root/SOURCE_REF")" || die 'Cannot read SOURCE_REF'
[[ "$expected" =~ ^[0-9a-f]{40}$ ]] || die 'SOURCE_REF must contain exactly one full lowercase Git SHA'
source_dir="${ORESLANG_SOURCE_DIR:-$root/.cache/compiler-$expected}"
[[ -d "$source_dir" ]] || die "Compiler checkout missing: $source_dir (run scripts/setup.sh)"
actual="$(git -C "$source_dir" rev-parse --verify HEAD)" || die 'Cannot read compiler HEAD'
[[ "$actual" == "$expected" ]] || die "Compiler is $actual, expected $expected"
source_status="$(git -C "$source_dir" status --porcelain --untracked-files=all)" || die 'Cannot inspect compiler worktree'
[[ -z "$source_status" ]] || die 'Compiler worktree has modified or untracked files'

if [[ "$mode" == --all ]]; then
  # Gitlinks and .gitmodules are tracked build inputs, not mutable suggestions.
  git -C "$root" diff --quiet HEAD -- .gitmodules dependencies/spin dependencies/otel \
    || die 'Submodule metadata or gitlink differs from HEAD'
  submodule_status="$(git -C "$root" submodule status --recursive)" || die 'Cannot inspect recursive submodules'
  [[ -n "$submodule_status" ]] || die 'No initialized submodules'
  while IFS= read -r line; do
    [[ "${line:0:1}" == ' ' ]] || die "Submodule uninitialized, diverged or conflicted: $line"
  done <<< "$submodule_status"
  for path in dependencies/spin dependencies/otel; do
    grep -Eq "^ [0-9a-f]{40} ${path}( |$)" <<< "$submodule_status" \
      || die "Missing pinned gitlink: $path"
  done
  # A matching gitlink alone does not detect dirty or untracked dependency files.
  git -C "$root" submodule foreach --quiet --recursive '
    git rev-parse --verify HEAD >/dev/null || exit 65
    dirty="$(git status --porcelain --untracked-files=all)" || exit 65
    if [ -n "$dirty" ]; then
      printf "Dirty dependency submodule: %s\n" "$displaypath" >&2
      exit 65
    fi
  ' || die 'One or more dependency submodules are dirty or unreadable'
fi
