# Oreslang demo RESTful server

A real HTTP/1.1 server on **127.0.0.1:3000**, built with Oreslang Spin and the native
HTTP runtime. Every matched request gets a fresh actor. Read handlers use shared
actors; mutation handlers use isolated actors that exclusively claim the moved
HTTP exchange.

## Run

Prerequisites: **JDK 25**, Maven, Git, Python 3.9+, curl, and GitHub access to the
Oreslang dependencies. Set `JAVA_HOME` to your JDK 25 installation. The startup
script uses its Java binary, or `ORES_JAVA` if explicitly set.

```bash
./scripts/setup.sh
./scripts/run-server.sh
```

In another terminal:

```bash
./scripts/curl-10.sh
```

The curl script executes **10 REST requests**, checks their status codes and
bodies, and prints each result. It covers GET, POST, PUT, PATCH, and DELETE. It
replaces and finally deletes the demo resource; use it against a demo instance.
Stop the server with Ctrl-C.

An existing clean compiler checkout can be reused with `ORESLANG_SOURCE_DIR` if
its commit matches `SOURCE_REF`. Otherwise setup clones and builds that exact
revision under `.cache/`. Spin, routing and oreslang-otel are pinned Git submodules. Setup
refuses to reset an existing compiler checkout.

Optional settings:

```bash
PORT=3001 DATA_DIR=/tmp/ores-rest-demo ./scripts/run-server.sh
BASE_URL=http://127.0.0.1:3001 ./scripts/curl-10.sh
./scripts/test.sh
```

## File routes

`src/routes/rest` is the root of REST routes. Directory names become literal URL
segments, and lowercase method filenames select the HTTP verb:

```text
src/routes/rest/
├── health/
│   └── get.ores                 GET /health
└── foo/bar/baz/
    ├── get.ores                 GET /foo/bar/baz
    ├── post.ores                POST /foo/bar/baz
    ├── put.ores                 PUT /foo/bar/baz
    ├── patch.ores               PATCH /foo/bar/baz
    └── delete.ores              DELETE /foo/bar/baz
```

Each method file declares its own `Handler` actor and exports
`route.handler(): HttpHandler`. The factory calls `http.handler("Handler")` in
that actor's declaring source unit. Copy an existing method file to add a route;
no central route list needs editing. Root-level `get.ores` maps to `/`.

At startup, `scripts/generate-routes.py` scans the tree and emits imports and
registrations into ignored `src/generated/routes.ores`. It supports `get`, `post`,
`put`, `patch`, `delete`, `head`, and `options`; rejects unknown method files and
symlinked/out-of-root route sources; and restricts directory names to literal
letters, digits, `_`, and `-`. Restart after changing the route tree. Dynamic
parameter directories are not implemented in this demo.

Incoming URLs are matched against the compiled route table. They never become
filesystem paths. Source files are not served as static content. Spin supplies
HEAD fallback, OPTIONS/Allow, 404 and 405 responses.

## Resource behavior

`/foo/bar/baz` represents one UTF-8 text resource, persisted in `data/baz.txt`:

| Method | Behavior | Status |
| --- | --- | --- |
| GET | Read the current representation | 200, or 404 if absent |
| POST | Create only when absent | 201 with Location, or 409 |
| PUT | Replace or create | 200, or 201 with Location |
| PATCH | Append the supplied text | 200, or 404 if absent |
| DELETE | Remove the resource | 204, or 404 if absent |

POST, PUT, and PATCH require `Content-Type: text/plain` and a nonempty UTF-8 body.
Other media types return 415; empty bodies return 400. PATCH is a documented text
append operation, not JSON Patch. Bodies are limited to 64 KiB per request.
`GET /health` returns `{"status":"ok"}`. Responses identify actor mode, and resource
responses identify their route source file through `X-Route-File`.

## Demo constraints

Admission is **one active request**, held until its actor finishes. This serializes
file operations within a single server process; excess concurrent requests get
503. The curl script retries only admission rejection. Use one server process per
data directory. This is deliberately a small persistence demo: file writes are
not crash-atomic database transactions, appended resources have no total-size
quota, and there is no authentication or production concurrency claim.

The server binds loopback, allows network access only to its configured local
port, and scopes file read/write permission to the data directory. Request paths
cannot choose a storage filename. Requests have a 10-second deadline. TLS,
JSON codecs, database transactions and graceful signal-driven draining are outside
this example; Ctrl-C terminates the development process.

Runtime isolation means exclusive guest authority over the request and actor-local
state, not OS-process isolation. The JVM transport retains the socket/FD.

## Validation

`./scripts/test.sh` runs 31 real-HTTP and route-generation checks: the 10-request
curl sequence, error responses, HEAD/OPTIONS, persistence across a server restart,
path mapping, unsupported method files and symlink rejection. The pinned Spin
revision also passed its existing 55 HTTP assertions and routing golden suite.
Validation used JDK 25 interpreter mode; native-image/optimizing-Graal performance
was not measured for this demo.

## Native AOT-only and hybrid builds

Select the native capability **at build time**:

```bash
# JAVA_HOME must point to GraalVM with native-image, matching the pinned SDK.
# ORESLANG_SOURCE_DIR must be a clean checkout at this repo's SOURCE_REF.
./scripts/build-native.sh aot
# Optional alternative with runtime compilation support:
./scripts/build-native.sh hybrid
```

The build generates the route registry, embeds the fixed application/import graph,
compiles a native application launcher, and packages the pthread carrier library
and license notices. It produces `dist/rest-server-aot-<os>-<arch>.tar.gz` (or
`rest-server-hybrid-...`). Python, Maven, Java, Git and compiler source are build
requirements only. The server in the resulting archive needs none of them.

After unpacking the matching platform archive:

```bash
./bin/rest-server --port=3000 --data-dir=./data
./bin/rest-server --build-info
# Another terminal, for the optional curl demo:
./curl-10.sh
```

`PORT` and `DATA_DIR` also work. Runtime configuration does not regenerate the
route registry. The native launcher extracts its embedded application into a
private temporary directory and supplies per-process configuration. It removes
that directory on normal termination/SIGTERM; SIGKILL may leave temporary files.
The packaged directory can be moved independently of the build tree.

AOT-only means the Java-written runtime/interpreter and reachable libraries are
native machine code, with **no guest JIT**. Oreslang handlers remain interpreted;
application-specific ahead-of-time machine-code lowering is a separate compiler
feature. `--mode=hybrid`/`--mode=jit` cannot enable JIT inside an AOT-only image.
Hybrid images accept `--mode=aot` to disable their guest JIT for a run. No external
JVM or GraalVM installation is required for either native build.

Validate the actual archive, including relocation, checksums, absent developer
tools, a required native carrier library, mode enforcement, and HTTP persistence:

```bash
python3 tests/native_distribution.py dist/rest-server-aot-darwin-arm64.tar.gz
```

macOS ARM64 AOT-only and hybrid archives were built and tested locally. Linux
packaging paths are supplied but not validated by these macOS results. These
executables depend on OS system libraries and the bundled carrier library; they
are not fully static, signed/notarized application releases. Native Image does
not alter the demo's single-admission file-store constraints.
## Native request telemetry

`dependencies/otel` pins [oreslang-otel](https://github.com/ores-truffle-oreslang/oreslang-otel),
whose only runtime dependency is the Oreslang standard library. Startup reads no
telemetry credentials and needs no collector. Stdout contains JSON Lines with
`schema: "oreslang-otel.v1"`, plus the existing human-readable startup messages.
Filter JSON records when consuming the log stream.

Every **admitted** request receives a new `X-Ores-Trace-Id` response header. Use
that value to correlate these spans and log records:

| Span | Interval |
| --- | --- |
| `http.server.request` | Transport admission through response closure and actor finalization, ending when the supervisor resumes |
| `routing.dispatch` | Spin routing and dispatch call, including any scheduling within that call |
| `actor.handle` | Handler claim/setup through awaited response and handler cleanup |

Routing/actor spans are children of the request span and can overlap. Do not
sum them to obtain total latency. Actor startup and move-to-claim delays are
separate histogram observations, also potentially overlapping. Durations use
monotonic nanoseconds; event timestamps use Unix milliseconds. The request span
and `http.server.request.duration` metric share the same duration sample.
`http.server.requests` emits one counter delta per completion;
`http.server.errors` emits one for 5xx/incomplete outcomes. A 4xx is a completed
HTTP request, not a server error. Status 0 denotes an incomplete/unknown outcome.

The supervisor captures a read-only completion future before dispatching the
exchange. It never reads a moved exchange. Handlers use actor-local spans and
finally blocks; GET/HEAD shared actors and mutation isolated actors both log.
Generated 404/405/OPTIONS responses have request and routing spans without a
handler span. Deadline/handler failures still settle the supervisor observation.
The demo remains serial: it waits for the admitted request to finalize before
accepting the next. This is intentional with its one-request admission policy.

Transport rejects before admission (for example oversized declared bodies or
capacity 503s) have no guest exchange, response trace header, or per-request
span. They remain transport counters. Process termination can interrupt exports.
Console serialization/I/O contributes overhead, so these are instrumented
latencies rather than uninstrumented performance benchmarks.

The adapter logs registered operation names, not raw URLs/query strings, request
bodies, cookies, authorization headers or arbitrary exception text. Incoming
trace headers are not adopted; this version provides local correlation, not W3C
propagation, OTLP export, or complete OpenTelemetry compatibility.

The integration suite verifies header correlation, exactly one completed request
span/metric, shared/isolated handlers, framework responses, no query leakage,
and a deliberately induced handler I/O failure.

### Finding a request in stdout

`curl-10.sh` prints `actor=shared|isolated`, `request_id`, `time` (seconds for
its final HTTP attempt), and the number of admission retries. The response's
`X-Request-Id` is the same UUID in the `actor.claim` log body; prefix it with
`ores-trace-` to find every related JSON record. Timestamps are Unix milliseconds.
The actor claim record includes mode, actual HTTP method, and registered operation.
The completed `http.server.request` span includes status and duration in nanoseconds.
Framework-generated responses use `X-Actor-Mode: supervisor` and have no actor claim.

GET/HEAD handlers are shared actors; PUT/POST/PATCH/DELETE handlers are isolated
actors. Logging passes correlation strings across ownership boundaries and creates
spans locally; it does not share a live span or revive a moved exchange.

Client timing includes network/response latency. It excludes earlier rejected
attempts and retry sleeps; the retry count makes those delays visible. Server
request spans include actor finalization, so they need not equal client timing.
Use startup and transfer metrics to distinguish actor overhead from handler work.
The single-admission file store and synchronous console export affect throughput.

### Truffle's deprecated Unsafe warning

The JDK 25 warning naming `NodeClassImpl$NodeFieldData` originates in the pinned
Truffle dependency, not an Ores request handler. Truffle's maintainers explain in
[oracle/graal#12782](https://github.com/oracle/graal/issues/12782) that its VM
integration still requires unsafe access and that replacing the deprecated access
path is upstream work. This application does not add direct `sun.misc.Unsafe`
usage or silence the warning. Removing it requires a compatible upstream change
and validation of both JVM and Native Image builds; changing HTTP ownership or
adding `--enable-native-access` does not remove that deprecated call.

## Async filesystem operations

The store awaits `fs.exists_async`, `read_text_async`, `write_text_async`,
`append_text_async`, and `remove_async`. Both shared GET actors and isolated
mutation actors release their execution carriers while filesystem work runs.
The portable runtime uses bounded host-I/O offload, not a promise of kernel
asynchronous disk I/O. HTTP body reads and response writes already use futures.
The one-request admission policy still serializes this demo's file-store updates.

Measure a handler (the root `/` is an unregistered 404 route):

```bash
curl --max-time 10 -sS -o /dev/null \
  -w 'status=%{http_code} connect=%{time_connect}s first_byte=%{time_starttransfer}s total=%{time_total}s\n' \
  http://127.0.0.1:3000/health
./scripts/curl-10.sh
python3 scripts/benchmark.py --runs 6
```

The benchmark runs six ten-request sequences (first is warm-up), then 20 health
requests with TCP connection, first-byte and total curl timings. `BASE_URL` selects
the server; it creates and deletes the demo resource. Run against a demo instance.

First-byte time includes connection setup and server work. It is not a direct
filesystem or routing duration. Console telemetry remains enabled and affects
these measurements.

## Core runtime performance diagnostics (experimental compiler pin)

The compiler pin on this development branch targets the diagnostic implementation
in [oreslang-source.java #451](https://github.com/ores-truffle-oreslang/oreslang-source.java/pull/451).
This is **core runtime diagnostics**, not userland `oreslang-otel`.

```sh
export JAVA_HOME="$(/usr/libexec/java_home -v 25)"
export PATH="$JAVA_HOME/bin:$PATH"
export ORESLANG_SOURCE_DIR="$PWD/.cache/compiler-$(tr -d '[:space:]' < SOURCE_REF)"
./scripts/setup.sh
PORT=3000 ./scripts/run-server.sh --core-perf 2>&1 | tee .cache/core-perf.log
```

In another terminal run `./scripts/curl-10.sh` repeatedly. Stop the server
with Ctrl-C to emit the bounded `ores-core-perf.v1` JSONL capture (stderr
is included in the tee command). Inspect with:

```sh
jq -Rc 'fromjson? | select(.schema == "ores-core-perf.v1" and .kind == "phase")
  | [.phase, (.duration_ns/1000000), (.start_ns/1000000)] | @tsv' .cache/core-perf.log
```

`ORES_CORE_PERF=true ./scripts/run-server.sh` is equivalent. Core diagnostics
record fixed event names and elapsed nanoseconds only; the demo still logs
`oreslang-otel.v1` separately when enabled. Compare overhead with core logging
off as well as on. The current capture flushes **on graceful JVM shutdown**,
not continuously.
