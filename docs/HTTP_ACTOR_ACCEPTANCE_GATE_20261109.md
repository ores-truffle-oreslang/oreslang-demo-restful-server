# HTTP actor acceptance: demo boundary and live read-only probe (2026-11-09)

This demo's `src/main.ores` uses a single-admission `http.listen(..., 1, ...)` and fresh actors via Spin. It does **not** declare or lower `define serial http actor`. Successful demo HTTP tests therefore do not certify serial-alias lowering, `requestConcurrency`, `messageConcurrency` or physical actor shutdown.

Start the demo using its pinned Java compiler, then run:

```bash
BASE_URL=http://127.0.0.1:3000 bash scripts/read-only-http-transport-smoke.sh
```

The opt-in Bash-only script restricts traffic to loopback, checks `SOURCE_REF` SHA syntax and the current one-admission/completion-observer source, issues only GET/HEAD requests, verifies distinct request IDs, and emits explicit **NOT_VERIFIED** markers. It does not send signals, write demo data, or prove source HTTP actor compilation, AOT/native behavior or physical shutdown.

## Minimal missing real execution gate

1. On the exact Java head compile both `define serial http actor Handler as end` and the equivalent explicit `requestConcurrency=1` / `messageConcurrency=1` source. Verify immutable lowered metadata; reject 3x4, invalid, duplicate and unbound transport configurations fail-closed until supported.
2. In a separate real guest HTTP fixture, hold a suspended handler, prove no simultaneous physical turns on one actor, cancel it, fence stale ownership and retry without double release.
3. During graceful shutdown complete the socket response while deliberately holding the actor carrier; require shutdown to remain pending or report an explicit timeout until actual physical quiescence. The current demo documents signal-driven graceful draining as unsupported: never count SIGTERM as proof.
4. Execute the unchanged guest with LLVM, Wasm, Forge and .NET AOT. Any unsupported backend must produce an executed compiler-owned negative diagnostic, not fabricated guest success. Pin source/compiler SHA and artifacts, actual stepful job logs and backend-specific runtime trace before declaring parity.

Shared acceptance draft: https://github.com/ores-truffle-oreslang/oreslang-platform-conformance/pull/642. Runtime candidates: Java #656/#997/#1166, LLVM #654/#1019, and .NET #228. This documentation and probe make none of those test claims.

## Hardening follow-up (2026-11-10)

The live read-only script additionally enforces **six unique UUID-shaped request IDs**,
corresponding `X-Ores-Trace-Id: ores-trace-<request_id>` headers, the
expected `shared` actor on GET/HEAD versus `supervisor` on unrouted 404,
and an exactly matching health JSON body. It refuses duplicate/missing
headers, non-loopback targets, implicit HTTP proxies, or redirects.
These are transport acceptance assertions, not proof of actor lowering.
The separate Bash fake-curl tests are host-only tests of the probe itself.


## Request-identity replay hardening (2026-11-11)

The probe rejects UUID replay even when a malicious test server varies the
uppercase/lowercase spelling of the same hex identity. Header values must have
exactly one whitespace-delimited field: appended untrusted words are rejected
rather than silently ignored. The fake-curl harness adds both negative cases;
this remains **HOST-ONLY** evidence, not guest execution.

At the exact PR head, route-publication CI [run 43042127324](https://github.com/ores-truffle-oreslang/oreslang-demo-restful-server/actions/runs/43042127324), job 130596411732, completed successfully with 13 executed steps including `Check read-only smoke script harness (fake curl, host only)`. The input-gate [run 43042127228](https://github.com/ores-truffle-oreslang/oreslang-demo-restful-server/actions/runs/43042127228), job 130596401430, also passed with seven executed steps. Neither test starts the real HTTP guest.
