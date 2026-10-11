# Dependency update — 2026-10-11

This is a reproducible **merged-main** dependency refresh of the RESTful demo.
It does not automatically opt into unmerged compiler, transport, actor, or
LLVM PRs. A SHA pin, not the moving name `main`, is the actual build input.

| Component | Previous revision | Pinned revision | Notes |
| --- | --- | --- | --- |
| Java/GraalVM Oreslang compiler | `4b0ff7dd4063df2906dc2fed0f6b6348b69807c6` | `a69035665047b4007ef3058cb4bd59aca8a8b8d1` | 54 merged commits ahead at review time; `SOURCE_REF` |
| Spin framework | `a961e235f4a5ea81cca99df401dd490642af7163` | `d34a34dd1409a53cdcd5f96797aabd9cc5748d77` | `dependencies/spin` gitlink; latest reviewed main |
| Oreslang OTel | `5fe62a3447e77072cfd30850a96ef654a18008f7` | unchanged | Already current; `dependencies/otel` gitlink |
| HTTP routing | inherited from Spin | Spin pins `41b2cbff35be3cb5dca7d33fe30c3892498f9b75` | Nested Spin gitlink; this demo does not override it |
| LLVM | not a runtime dependency | not added | LLVM backend parity is verified separately |

The Spin repository also has its own historical `SOURCE_REF` for *its own*
validation; this application intentionally selects the newer compiler using
the top-level `SOURCE_REF`. The Spin launcher reads `ORESLANG_SOURCE_DIR`
and does not require the application to use Spin's historical validation pin.

## Setup and validation

Requires GitHub access to the private Java, Spin, OTel, and HTTP routing repos,
JDK 25 (GraalVM for native-image), Maven, Git, Python 3.9+, and curl.

```sh
./scripts/setup.sh
./scripts/test.sh
# Separately, with a GraalVM 25 native-image installation:
./scripts/build-native.sh aot
python3 tests/native_distribution.py dist/rest-server-aot-<os>-<arch>.tar.gz
./scripts/build-native.sh hybrid
python3 tests/native_distribution.py dist/rest-server-hybrid-<os>-<arch>.tar.gz
```

By default the compiler lives at `.cache/compiler-$(cat SOURCE_REF)`.
The setup script will not mutate a pre-existing mismatched or dirty checkout.
Override `ORESLANG_SOURCE_DIR` to use an explicitly selected clean checkout
at exactly the pinned commit. Git submodules are initialized recursively and
their gitlink commits remain exact.

The public GitHub Actions workflow `static-ci.yml` validates Python/shell
syntax, dependency-pin shape and gitlink types **without** fetching the private
compiler and submodules. Its success is not an HTTP or GraalVM conformance
result. The 10-request real HTTP suite, core diagnostic shutdown checks, native
AOT/hybrid distribution checks, and actual runtime behavior require an
authenticated JDK 25 build and are release gates.

## Compatibility and security boundaries

* The Ores source keeps its existing explicit HTTP exchange move/claim contract.
  The supervisor saves `request.completion` before dispatch; each handler
  awaits the HTTP response, ends itself, and finalizes its telemetry span.
* `request.respond(...)` is the HTTP transport response; it is not an actor
  message `self.respond(...)`. These APIs should not be interchanged.
* The one-admitted-request file-store policy remains intentional. Do **not**
  infer new actor-serialization, durable storage, or multi-process safety from
  compiler advances.
* The current compiler main includes only **merged** features. Unmerged
  supervision, `match`/return, serial actor, typed transport, pre-emption and
  continuation drafts are not automatically enabled by this pin.
* Dependency updates are not security certification. Do not merge until the
  pinned real-HTTP and native tests are run, their head commits are verified,
  and CI failures/runner allocation are understood.

## Hardening follow-up

All execution entrypoints now invoke `scripts/check-deps.sh --all` before
building/running. The shared verifier rejects malformed `SOURCE_REF`, missing,
mismatched or dirty compiler trees, uninitialized/diverged recursive gitlinks,
modified `.gitmodules`, and modified or untracked files inside **every**
submodule (including Spin's nested router). Git command failures are fatal,
not silently converted into empty/clean status output.

Public offline CI exercises clean, dirty, absent, mismatched, malformed and
Git-failure cases using temporary local repositories, including a nested
Git submodule. Runtime authentication and JDK 25 HTTP/AOT/hybrid validation
remain separate required evidence; offline regression success does not
establish runtime compatibility.

Setup also validates the exact SHA and unchanged submodule declarations *before*
starting a network fetch or checking out private compiler code. A modified
`.gitmodules` or malformed `SOURCE_REF` cannot direct setup to an unexpected
remote. A pre-existing compiler path (including a symbolic link) is never
silently replaced by cloning.
