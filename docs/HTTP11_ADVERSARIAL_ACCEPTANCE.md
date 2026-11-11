# HTTP/1.1 adversarial acceptance (real guest execution required)

The new **Rust-only, dependency-free** harness
`tools/http-negative/src/main.rs` runs strict raw HTTP/1.1 tests against
a disposable AttoLang/Spin server on loopback. It starts the actual demo
runtime, writes one sentinel resource, verifies exact persisted bytes after
each invalid request, and probes GET and /health for recovery.

## Host-only unit checks (safe in public CI)

```sh
cargo test --offline --manifest-path tools/http-negative/Cargo.toml
```

These tests check the harness's wire framing, case uniqueness, response parser
and fail-closed classifier. They **DO NOT** run the AttoLang compiler, Spin
HTTP transport, actor handler, native image, or guest AOT backend.

## Real guest execution (authorized development machine/runner)

Requires JDK 25, Maven and access to the private pinned Java compiler and
Spin submodules. The harness creates its own data directory and loopback port;
it never targets an external server.

```sh
./scripts/setup.sh
cargo run --offline --manifest-path tools/http-negative/Cargo.toml -- --run
```

To exercise a built native launcher instead, set
`REST_SERVER_BINARY=/absolute/path/to/rest-server` before the cargo command.
This changes the runtime under test; do not describe GraalVM native image as
guest application AOT compilation.

## Strict negative cases

- Conflicting and identical duplicate Content-Length; Content-Length plus
  Transfer-Encoding; unsupported Transfer-Encoding, invalid Content-Length,
  invalid chunk size
- NUL and obsolete-folded headers, bare-LF header framing
- Truncated fixed-length body, invalid UTF-8, declared and decoded chunked
  bodies above 64 KiB
- Unsupported HTTP version and attempted smuggled second request
- After every denial, exact sentinel persistence, successful GET and /health

The desired contract accepts only an explicit 400/408/413/414/422/431/505 or
a fully closed transport without any response. A 503 is *inconclusive* (retry). Per RFC 9112 §6.1, the **unsupported Transfer-Encoding**
case may return HTTP 501 when the connection closes, as may the folded
unsupported Transfer-Encoding value. RFC 9112 §5.2 also permits a server to
**unfold** an obsolete folded field rather than reject it. Therefore the
folded Content-Type case accepts a closed HTTP 415 only when its canonicalized
value fails application media-type validation and the persisted sentinel is
unchanged. Folded Content-Length must not be accepted as a valid length; a
folded Transfer-Encoding must not be interpreted as plain chunked. None of
these exceptions extend to unrelated malformed requests.
An unexpected 200/201/204, 409, 500, a smuggled 200 or hung connection fails.
The formerly named "bare LF framing" and "unsupported HTTP version"
probes now test **read-only GET /health** interoperability instead: RFC 9112
permits recognition of LF-only line endings, and RFC 9110 recommends
same-major higher-minor fallback. They accept HTTP 200 **only if** the
connection closes and the exact decoded body is `{"status":"ok"}`; they
never authorize a mutating POST or general success to malformed input.

The truncated Content-Length probe includes a valid `Content-Type:
text/plain` so the actor actually attempts the body read. Strict malformed
UTF-8 bodies must be classified as 400 at the Java HTTP request-body
boundary, without misclassifying unrelated response failures. On the
historic Java pin this requires an explicitly labeled compiler patch
overlay; the original compiler SHA/tree cannot be called patched.

This is a strict *target*, not a claim that the pinned runtime passes yet.

## Acceptance evidence before merging as a guest gate

For every executed run record demo commit SHA, compiler SHA in SOURCE_REF,
dependency submodule SHAs, executable hash, OS, runner identity, command,
exit status, logs and nonzero steps. Classify JVM interpreter/native launcher
and truly compiled guest AOT separately. Mark unexecuted, skipped or zero-step
runs `NOT_EXECUTED`. The compiler/Spin dependencies are private; public-demo
Actions cannot assume cross-repository access. Never loosen the strict
`ci/source-policy.sh` Python prohibition or export private sources without
explicit destination authorization and identity verification.

## Hardening follow-up (2026-11-10)

The harness requires the server to announce its selected listening port
before trusting a response, rejects partial writes, incomplete replies and
multiple response status lines (including two 4xx replies), and decodes
`Content-Length` or chunked baseline bodies for byte-for-byte equality.
Failure receipts on Unix are created in a private 0700 temporary directory;
a custom `REST_SERVER_BINARY` must be an absolute path. A correct HTTP
status alone does **not** prove the connection reached EOF or the resource
representation was preserved.


A strict case with an unexpected response is recorded as `NOT_VERIFIED`;
the harness continues checking subsequent cases **only while persisted
bytes remain unchanged and GET/health recover**, then exits nonzero with all
outstanding cases listed. A mutated resource or poisoned baseline aborts
immediately. This avoids one nonsecurity protocol-compatibility response
hiding later request-smuggling vulnerabilities.
