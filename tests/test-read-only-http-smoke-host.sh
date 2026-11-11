#!/usr/bin/env bash
# HOST-ONLY smoke harness self-test. Fake curl, no guest compiler/runtime.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat > "$tmp/bin/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
headers=''
body=''
head_only=0
while (($#)); do
  case "$1" in
    -D) headers="$2"; shift 2;;
    -o) body="$2"; shift 2;;
    -I) head_only=1; shift;;
    -w|--connect-timeout|--max-time|--noproxy|--proto) shift 2;;
    -sS) shift;;
    http://*) url="$1"; shift;;
    *) echo "unexpected fake curl arg: $1" >&2; exit 3;;
  esac
done
count="$(cat "$FAKE_CURL_COUNT")"
count=$((count+1))
printf '%s\n' "$count" > "$FAKE_CURL_COUNT"
code=200
if [[ "$url" != */health ]]; then code=404; fi
if [[ "${FAKE_MODE:-good}" == bad_404 && "$url" != */health ]]; then code=200; fi
if [[ "${FAKE_MODE:-good}" == bad_head && $head_only -eq 1 ]]; then code=503; fi
if [[ -n "$headers" ]]; then
  printf 'HTTP/1.1 %s Synthetic\r\n' "$code" > "$headers"
  if [[ "${FAKE_MODE:-good}" != missing_id ]]; then
    id=$count
    if [[ "${FAKE_MODE:-good}" == duplicate ]]; then id=1; fi
    request_id="$(printf '00000000-0000-0000-0000-%012d' "$id")"
    if [[ "${FAKE_MODE:-good}" == malformed_id ]]; then request_id='------------------------------------'; fi
    if [[ "${FAKE_MODE:-good}" == duplicate_case ]]; then
      request_id='abcdefab-cdef-abcd-abcd-000000000001'
      if ((count > 1)); then request_id='ABCDEFAB-CDEF-ABCD-ABCD-000000000001'; fi
    fi
    if [[ "${FAKE_MODE:-good}" == extra_id_field ]]; then
      printf 'X-Request-Id: %s ignored\r\n' "$request_id" >> "$headers"
    else
      printf 'X-Request-Id: %s\r\n' "$request_id" >> "$headers"
    fi
    if [[ "${FAKE_MODE:-good}" == duplicate_header ]]; then
      printf 'X-Request-Id: %s\r\n' "$request_id" >> "$headers"
    fi
    trace="ores-trace-$request_id"
    if [[ "${FAKE_MODE:-good}" == wrong_trace ]]; then trace='ores-trace-wrong'; fi
    printf 'X-Ores-Trace-Id: %s\r\n' "$trace" >> "$headers"
  fi
  actor=shared
  if [[ "$url" != */health ]]; then actor=supervisor; fi
  if [[ "${FAKE_MODE:-good}" == wrong_actor ]]; then actor=isolated; fi
  printf 'X-Actor-Mode: %s\r\n' "$actor" >> "$headers"
  printf '\r\n' >> "$headers"
fi
if [[ -n "$body" && "$body" != /dev/null ]]; then
  if [[ "${FAKE_MODE:-good}" == bad_body ]]; then
    printf '{"status":"bad"}' > "$body"
  else
    printf '{"status":"ok"}' > "$body"
  fi
fi
printf '%s' "$code"
CURL
chmod +x "$tmp/bin/curl"
export PATH="$tmp/bin:$PATH"
export FAKE_CURL_COUNT="$tmp/count"
export BASE_URL="http://127.0.0.1:3000"
test_good() {
  printf '0\n' > "$FAKE_CURL_COUNT"
  FAKE_MODE=good bash "$root/scripts/read-only-http-transport-smoke.sh" > "$tmp/out" 2> "$tmp/err"
  grep -Fq 'LIVE_HTTP_LOOPBACK=PASS' "$tmp/out"
  grep -Fq 'FIVE_BACKEND_GUEST_PARITY=NOT_VERIFIED' "$tmp/out"
  [[ "$(cat "$FAKE_CURL_COUNT")" == 6 ]]
}
test_fail() {
  local mode="$1" message="$2"
  printf '0\n' > "$FAKE_CURL_COUNT"
  if FAKE_MODE="$mode" bash "$root/scripts/read-only-http-transport-smoke.sh" > "$tmp/out" 2> "$tmp/err"; then
    echo "fake $mode passed unexpectedly" >&2; exit 1
  fi
  if [[ -n "$message" ]]; then grep -Fq "$message" "$tmp/err"; fi
  ! grep -Fq 'LIVE_HTTP_LOOPBACK=PASS' "$tmp/out"
}
test_good
test_fail duplicate 'request identity reused'
test_fail duplicate_case 'request identity reused'
test_fail extra_id_field 'missing/invalid request identity'
test_fail missing_id 'missing/duplicate request identity'
test_fail duplicate_header 'missing/duplicate request identity'
test_fail malformed_id 'missing/invalid request identity'
test_fail wrong_trace 'trace/request identity mismatch'
test_fail wrong_actor 'unexpected actor mode'
test_fail bad_body 'health body mismatch'
test_fail bad_head 'HEAD /health expected 200'
test_fail bad_404 'missing route expected 404'
for invalid in http://localhost:3000 http://0.0.0.0:3000 https://127.0.0.1:3000 \
               http://127.0.0.1:0 http://127.0.0.1:65536 \
               http://127.0.0.1:3000/health http://127.0.0.1:3000@evil.example; do
  if BASE_URL="$invalid" bash "$root/scripts/read-only-http-transport-smoke.sh" > "$tmp/out" 2> "$tmp/err"; then
    echo "invalid endpoint accepted: $invalid" >&2; exit 1
  fi
done
echo 'PASS host-only: 1 success, 11 response negatives, 7 URL rejects; no guest executed'
