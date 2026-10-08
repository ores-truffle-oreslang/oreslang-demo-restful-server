#!/usr/bin/env bash
# Ten REST requests. This exercises and finally deletes the demo resource.
set -euo pipefail
base="${BASE_URL:-http://127.0.0.1:3000}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
count=0
request() {
  local expected="$1" method="$2" path="$3" expected_body="$4"
  shift 4
  local status attempt result seconds mode request_id
  # 503 means admission was rejected before any handler ran; retry only that case.
  for attempt in {1..20}; do
    result="$(curl --silent --show-error --max-time 10 --request "$method" \
      --dump-header "$tmp/headers" --output "$tmp/body" --write-out '%{http_code} %{time_total}' \
      "$base$path" "$@")"
    read -r status seconds <<< "$result"
    [[ "$status" != 503 ]] && break
    sleep 0.05
  done
  [[ " $expected " == *" $status "* ]] || { echo "$method $path: expected $expected, got $status" >&2; cat "$tmp/body" >&2; exit 1; }
  [[ "$(cat "$tmp/body")" == "$expected_body" ]] || { echo "$method $path: unexpected body" >&2; cat "$tmp/body" >&2; exit 1; }
  count=$((count + 1))
  mode="$(awk 'tolower($1) == "x-actor-mode:" {gsub("\\r", "", $2); print $2}' "$tmp/headers")"
  request_id="$(awk 'tolower($1) == "x-request-id:" {gsub("\\r", "", $2); print $2}' "$tmp/headers")"
  printf '%02d %-6s %-32s -> %s actor=%s request_id=%s time=%ss retries=%s\n' \
    "$count" "$method" "$path" "$status" "${mode:-transport}" "${request_id:-none}" "$seconds" "$((attempt - 1))"
}
request '200' GET /health '{"status":"ok"}'
request '200 201' PUT /foo/bar/baz 'first version' -H 'Content-Type: text/plain' --data-binary 'first version'
request '200' GET /foo/bar/baz 'first version'
request '409' POST /foo/bar/baz 'resource already exists' -H 'Content-Type: text/plain' --data-binary 'duplicate'
request '200' PATCH /foo/bar/baz 'first version + patch' -H 'Content-Type: text/plain' --data-binary ' + patch'
request '200' GET '/foo/bar/baz?view=updated' 'first version + patch'
request '204' DELETE /foo/bar/baz ''
request '404' GET /foo/bar/baz 'resource not found'
request '201' POST /foo/bar/baz 'created again' -H 'Content-Type: text/plain' --data-binary 'created again'
request '204' DELETE /foo/bar/baz ''
echo 'PASS: 10 REST requests'
