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
    -w|--connect-timeout|--max-time) shift 2;;
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
    printf 'X-Request-Id: 00000000-0000-0000-0000-%012d\r\n' "$id" >> "$headers"
  fi
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
test_fail missing_id 'missing/invalid request identity'
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
echo 'PASS host-only: 1 success, 5 response negatives, 7 URL rejects; no guest executed'
