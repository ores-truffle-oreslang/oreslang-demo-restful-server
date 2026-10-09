#!/usr/bin/env bash
# Sequential six-round real HTTP comparison (first round is warm-up).
# Requires JDK 25, jq, and a compiler checkout prepared by ./scripts/setup.sh.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
command -v jq >/dev/null || { echo 'jq required' >&2; exit 69; }
port="${PORT:-31031}"
source_dir="${ORESLANG_SOURCE_DIR:-$root/.cache/oreslang-source.java}"
expected="$(tr -d '[:space:]' < SOURCE_REF)"
[[ "$(git -C "$source_dir" rev-parse HEAD 2>/dev/null)" == "$expected" ]] || {
  echo "Run ./scripts/setup.sh for pinned compiler $expected first" >&2; exit 65;
}
if curl -sS --max-time 1 "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
  echo "Port $port is already in use; choose another PORT" >&2; exit 73
fi
report="$root/.cache/profile-core-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$report"
printf 'Demo revision: %s\nCompiler: %s\nArtifacts: %s\n' "$(git rev-parse HEAD)" "$expected" "$report"
pid=''
stop_server() {
  if [[ -n "$pid" ]]; then
    kill -TERM "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    pid=''
  fi
}
trap stop_server EXIT
summarize() {
  local times="$1" label="$2" sorted=() n p50 p95
  mapfile -t sorted < <(sort -n "$times")
  n="${#sorted[@]}"
  (( n == 50 )) || { echo "Expected 50 warm requests, found $n" >&2; exit 1; }
  p50=$(((50*n + 99)/100))
  p95=$(((95*n + 99)/100))
  printf '%s n=%d p50=%sms p95=%sms max=%sms\n' \
    "$label" "$n" "${sorted[p50-1]}" "${sorted[p95-1]}" "${sorted[n-1]}"
}
for mode in baseline instrumented; do
  run_dir="$report/$mode"
  mkdir -p "$run_dir/data"
  log="$run_dir/server.log"
  if [[ "$mode" == baseline ]]; then
    flags=(--no-core-perf --no-core-debug)
  else
    flags=(--core-perf --core-debug)
  fi
  PORT="$port" DATA_DIR="$run_dir/data" ./scripts/run-server.sh "${flags[@]}" >"$log" 2>&1 &
  pid=$!
  ready=0
  for ((attempt=0; attempt<1200; attempt++)); do
    if grep -q 'Listening on http://127.0.0.1:' "$log"; then ready=1; break; fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "Server exited before readiness ($mode)" >&2; tail -80 "$log" >&2; exit 1
    fi
    sleep 0.1
  done
  (( ready )) || { echo "Server not ready ($mode)" >&2; tail -80 "$log" >&2; exit 1; }
  : > "$run_dir/warm-ms.txt"
  : > "$run_dir/curl.log"
  for ((round=0; round<6; round++)); do
    output="$(BASE_URL="http://127.0.0.1:$port" ./scripts/curl-10.sh)" || {
      echo "curl-10 failed: mode=$mode round=$round" >&2; exit 1;
    }
    printf '%s\n' "$output" >> "$run_dir/curl.log"
    grep -q 'PASS: 10 REST requests' <<<"$output" || exit 1
    if ((round > 0)); then
      awk '{ for(i=1;i<=NF;i++) if($i ~ /^time=[0-9.]+s$/) {
        split($i, a, "="); sub(/s$/, "", a[2]); print 1000*a[2]
      }}' <<<"$output" >> "$run_dir/warm-ms.txt"
    fi
  done
  for ((attempt=0; attempt<100; attempt++)); do
    spans="$(jq -Rn '[inputs | fromjson? | select(.schema == "oreslang-otel.v1" and .signal == "span" and .name == "http.server.request")] | length' "$log")"
    (( spans >= 60 )) && break
    sleep 0.05
  done
  stop_server
  summarize "$run_dir/warm-ms.txt" "$mode warm HTTP final-attempt"
  jq -Rn '[inputs | fromjson? | select(.schema == "oreslang-otel.v1" and .signal == "span" and .name == "http.server.request") | .duration_ns / 1000000] | sort | {count:length, p50_ms: .[((length*50+99)/100|floor)-1], p95_ms: .[((length*95+99)/100|floor)-1]}' "$log" > "$run_dir/userland-summary.json"
  if [[ "$mode" == instrumented ]]; then
    jq -Rn '[inputs | fromjson? | select(.schema == "ores-core-perf.v1" and .kind == "phase")] | group_by(.phase) | map(. as $g | ($g | map(.duration_ns / 1000000) | sort) as $s | {phase: $g[0].phase, count: ($s | length), p50_ms: $s[((($s|length)*50+99)/100|floor)-1], p95_ms: $s[((($s|length)*95+99)/100|floor)-1]})' "$log" > "$run_dir/core-phase-summary.json"
    jq -eRn '[inputs | fromjson? | select(.schema == "ores-core-perf.v1" and .kind == "phase")] | length > 0' "$log" >/dev/null || {
      echo 'No core performance records after SIGTERM' >&2; exit 1;
    }
    jq -eRn '[inputs | fromjson? | select(.schema == "ores-core-debug.v1" and .kind == "event")] | length > 0' "$log" >/dev/null || {
      echo 'No core debug records after SIGTERM' >&2; exit 1;
    }
  fi
done
printf 'Saved raw HTTP samples, logs, OTel summaries and core phases: %s\n' "$report"
echo 'Core phases and userland spans can overlap; do not add their durations.'
