#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$root/scripts/lib/guards.sh"

ores_verify_submodule_contract "$root"
valid="$(ores_read_exact_source_ref "$root")"
[[ "$valid" =~ ^[0-9a-f]{40}$ ]]

tmp="$(mktemp -d "${TMPDIR:-/tmp}/ores-rest-guards.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
cp "$root/.gitmodules" "$tmp/.gitmodules"
printf 'not-a-sha\n' > "$tmp/SOURCE_REF"

if ores_read_exact_source_ref "$tmp" >/dev/null 2>&1; then
  echo "invalid SOURCE_REF was accepted" >&2
  exit 1
fi

cp "$root/SOURCE_REF" "$tmp/SOURCE_REF"
git config -f "$tmp/.gitmodules" submodule.dependencies/spin.url https://example.invalid/evil.git
if ores_verify_submodule_contract "$tmp" >/dev/null 2>&1; then
  echo "unexpected submodule URL was accepted" >&2
  exit 1
fi

cp "$root/.gitmodules" "$tmp/.gitmodules"
git config -f "$tmp/.gitmodules" submodule.extra.path dependencies/extra
git config -f "$tmp/.gitmodules" submodule.extra.url https://example.invalid/extra.git
if ores_verify_submodule_contract "$tmp" >/dev/null 2>&1; then
  echo "unexpected extra submodule was accepted" >&2
  exit 1
fi

echo "source/submodule guard tests passed"
