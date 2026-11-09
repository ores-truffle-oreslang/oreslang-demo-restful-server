#!/usr/bin/env bash
# Read-only live HTTP probe. Not a serial-http-actor lowering or shutdown test.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base="${BASE_URL:-http://127.0.0.1:3000}"
if [[ ! "$base" =~ ^http://127\.0\.0\.1:([0-9]{1,5})$ ]]; then
  echo "BASE_URL must be loopback HTTP with an explicit port" >&2
  exit 2
fi
port="${BASH_REMATCH[1]}"
if (( 10#$port < 1 || 10#$port > 65535 )); then
  echo "invalid loopback port" >&2
  exit 2
fi
command -v curl >/dev/null || { echo "curl missing" >&2; exit 2; }
pin="$(tr -d '\r\n' < "$root/SOURCE_REF")"
[[ "$pin" =~ ^[0-9a-f]{40}$ ]] || { echo "SOURCE_REF must be exact SHA" >&2; exit 2; }
grep -Fq 'http.listen("127.0.0.1", config.port(), 1, 65536, 10000)' "$root/src/main.ores" ||
  { echo "listener source changed; audit first" >&2; exit 2; }
grep -Fq 'val int status = await completion;' "$root/src/main.ores" ||
  { echo "completion observer changed; audit first" >&2; exit 2; }
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
seen='|'
for n in 1 2 3 4; do
  code="$(curl -sS -D "$tmp/headers" -o "$tmp/body" --connect-timeout 3 --max-time 12 -w '%{http_code}' "$base/health")"
  [[ "$code" == 200 ]] || { echo "GET /health expected 200, got $code" >&2; exit 1; }
  grep -Fq '"status":"ok"' "$tmp/body" || { echo "health body mismatch" >&2; exit 1; }
  id="$(awk 'tolower($1)=="x-request-id:" {gsub(/\r/,"",$2); print $2}' "$tmp/headers" | tail -n 1)"
  [[ "$id" =~ ^[0-9a-fA-F-]{36}$ ]] || { echo "missing/invalid request identity" >&2; exit 1; }
  [[ "$seen" != *"|$id|"* ]] || { echo "request identity reused" >&2; exit 1; }
  seen="$seen$id|"
done
code="$(curl -sS -o /dev/null --connect-timeout 3 --max-time 12 -w '%{http_code}' -I "$base/health")"
[[ "$code" == 200 ]] || { echo "HEAD /health expected 200, got $code" >&2; exit 1; }
code="$(curl -sS -o /dev/null --connect-timeout 3 --max-time 12 -w '%{http_code}' "$base/no-such-http-actor-audit-route")"
[[ "$code" == 404 ]] || { echo "missing route expected 404, got $code" >&2; exit 1; }
echo "LIVE_HTTP_LOOPBACK=PASS SOURCE_REF=$pin REQUESTS=6"
echo "SERIAL_HTTP_ACTOR_LOWERING=NOT_VERIFIED"
echo "TWO_LEVEL_CONCURRENCY=NOT_VERIFIED"
echo "PHYSICAL_SHUTDOWN_QUIESCENCE=NOT_VERIFIED"
echo "FIVE_BACKEND_GUEST_PARITY=NOT_VERIFIED"
