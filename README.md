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
