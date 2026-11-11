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
a fully closed transport without any response. A 503 is *inconclusive* (retry).
An unexpected 200/201/204, 409, 500, a smuggled 200 or hung connection fails.
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
