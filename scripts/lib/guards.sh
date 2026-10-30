#!/usr/bin/env bash

ores_read_exact_source_ref() {
  local root="$1"
  local ref
  ref="$(tr -d '[:space:]' < "$root/SOURCE_REF")"
  if [[ ! "$ref" =~ ^[0-9a-f]{40}$ ]]; then
    echo "SOURCE_REF must contain exactly one lowercase 40-character commit SHA." >&2
    return 64
  fi
  printf '%s\n' "$ref"
}

ores_verify_submodule_contract() {
  local root="$1"
  local file="$root/.gitmodules"
  [[ -f "$file" ]] || { echo ".gitmodules is required." >&2; return 65; }

  [[ "$(git config -f "$file" --get submodule.dependencies/spin.path || true)" == "dependencies/spin" ]] ||
    { echo "Unexpected Spin submodule path." >&2; return 65; }
  [[ "$(git config -f "$file" --get submodule.dependencies/spin.url || true)" == "https://github.com/ores-truffle-oreslang/oreslang-spin.git" ]] ||
    { echo "Unexpected Spin submodule URL." >&2; return 65; }
  [[ "$(git config -f "$file" --get submodule.dependencies/otel.path || true)" == "dependencies/otel" ]] ||
    { echo "Unexpected OTel submodule path." >&2; return 65; }
  [[ "$(git config -f "$file" --get submodule.dependencies/otel.url || true)" == "https://github.com/ores-truffle-oreslang/oreslang-otel.git" ]] ||
    { echo "Unexpected OTel submodule URL." >&2; return 65; }

  local paths urls
  paths="$(git config -f "$file" --get-regexp '^submodule\..*\.path$' | wc -l | tr -d '[:space:]')"
  urls="$(git config -f "$file" --get-regexp '^submodule\..*\.url$' | wc -l | tr -d '[:space:]')"
  [[ "$paths" == "2" && "$urls" == "2" ]] ||
    { echo "Unexpected additional submodule entries." >&2; return 65; }
}
