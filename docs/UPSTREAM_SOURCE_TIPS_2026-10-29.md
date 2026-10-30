# REST demo upstream source-tip audit — 2026-10-29

**Scope:** `ores-truffle-oreslang/oreslang-demo-restful-server`. Default-branch compiler pin `SOURCE_REF=4b0ff7dd4063df2906dc2fed0f6b6348b69807c6`; **not** silently upgraded. The native route/application and HTTP integration tests must continue to be distinguished from static guards.

## P0: Python-to-Rust migration (blocked)

The Java reference compiler `main` at inspected commit `9f4f4a3b75dd4b124e8509b65a88a98213c11ab5` prohibits Python in any Oreslang repo, including test generators, benchmarks and CI. Existing open tracking issue: [#17](https://github.com/ores-truffle-oreslang/oreslang-demo-restful-server/issues/17). Six current Python files on the default branch are:

- `scripts/benchmark.py`
- `scripts/generate-routes.py`
- `tests/core_diagnostics.py`
- `tests/integration.py`
- `tests/native_distribution.py`
- `tests/test_route_publication.py`

`scripts/generate-routes.py` is used for route publication; `tests/test_route_publication.py` verifies the generator; `scripts/test.sh` invokes Python integration/diagnostic tests; `.github/workflows/route-publication.yml` invokes Python unittest. **None of these can be removed without replacing their behavior and regression evidence.** Port to Rust (with cargo test/clippy/fmt) and switch every invoker before claiming organization policy compliance. Until then the source toolchain remains noncompliant.

The new structural workflow is an **interim no-new-Python guard**: it allows ONLY these already tracked paths while migration is underway, emits `LEGACY_PYTHON_NONCOMPLIANT`, and rejects any newly introduced .py/.pyi/.ipynb files. It is **not** a waiver, a native compiler pass, or a completed migration.

## Runtime/source integration acceptance

- Keep `SOURCE_REF` immutable until a genuine upgraded Java/Graal compiler (and pinned Spin/router/otel Git submodule SHAs) checks and executes all guest routes, GET/POST/PUT/PATCH/DELETE and capability-denial/fault/parallel-shutdown oracles. Do not repin to an arbitrary open compiler PR (#10/#12/#14 cover distinct upgrade paths).
- Preserve exact source-file/route-root admission, no symlink path traversal, no untrusted file serving, bounded HTTP bodies, safe file persistence, one owner for mutable HTTP exchanges and careful 404/405/500/close lifecycle.
- Require deny-by-default host effects; use `flags-2-env` configuration as documented, with permission flags checked against canonical paths and endpoints. Don't infer security or parity from a host-only test.
- Prefer `snake_case` and admitted ownership forms `rt borrow/take/copy/share`. Java/Graal, LLVM, WASM, .NET and Forge have separate implementation and conformance evidence; never claim five-backend parity from the Java native HTTP host.
- Require exact demo tree/blob hashes, compiler source/build identity and SHA-256, runner ID, nonempty steps, real request/response oracle, negative/timeout tests and independent protected-main review. Zero-step Actions is `NOT_EXECUTED`.
- Never transfer private compiler source to another CI org without explicit authorization and verified provenance; do not use cross-org secrets or private checkout tokens.

## Evidence labels

`STRUCTURAL_ONLY` = tracked-file hygiene and pin-format checks. `LEGACY_PYTHON_NONCOMPLIANT` = known migration still open. `NOT_EXECUTED` = no native compiler/guest execution in this particular gate. A separate trusted integration workflow must establish executable results before release.
