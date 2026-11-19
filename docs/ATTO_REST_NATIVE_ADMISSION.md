# AttoLang native REST demo admission boundary

This demo's real application is `src/main.ores`, with asynchronous HTTP listening, actor handlers, telemetry, mutable data, and Spin dependencies. **Do not present scalar compilation as HTTP server or multi-backend parity.** That full source currently exceeds the documented native LLVM and Rust/Cranelift frontend subsets.

`tests/guest/rest_status_scalar.atto` is a canonical AttoLang **executable scalar** canary with named REST-status functions. The independent compiler must emit a real native object/LLVM IR, link it, and execute it with exit code 42. `tests/guest/rest_http_actor_unimplemented.atto` is a negative admission fixture: scalar backends must fail closed, without an object or executable IR.

Run `bash ci/test-atto-rest-aot.sh rs /absolute/path/to/oresc` or `bash ci/test-atto-rest-aot.sh llvm /absolute/path/to/oreslang-llvmc` on an authorized runner containing the exact compiler source; do not substitute source text inspection or invoke a Java/other backend as a fake.

CI evidence should record exact compiler tree SHA, demo fixture blob SHA, host compiler version, real executable exit code, and negative admission. Until both native backends pass, all `.atto` HTTP transport, actors, full-AOT application execution, and real-time guarantees remain **NOT_EXECUTED / UNSUPPORTED**; the parent demo still uses pinned `.ores` sources for compatibility.
