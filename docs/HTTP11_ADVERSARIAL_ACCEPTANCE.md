# HTTP/1.1 adversarial acceptance (guest execution required)

This change adds `tests/http_adversarial.py`, a black-box raw-socket probe
against the **actual** demo compiler, Spin transport and AttoLang handlers.
It does not substitute a fake server for compiler/runtime execution.

## Run in an authorized compiler environment

Prerequisites: JDK 25, Maven, pinned Git submodules and permission to fetch the
private compiler and Spin repositories. This probe starts a temporary loopback
server using the existing `tests.integration.Server` harness, installs a single
disposable resource, and verifies its exact bytes after each malformed request.

```bash
./scripts/setup.sh
python3 tests/http_adversarial.py
```

Optionally use a previously built native archive by setting
`REST_SERVER_BINARY=/absolute/path/to/rest-server` (the same harness supports
it). Run `python3 -m unittest discover -s tests -p
'test_http_adversarial_host.py' -v` for **host-only harness self-tests**.

## Negative contract

- Conflicting and identical duplicate Content-Length, conflicting
  Content-Length/Transfer-Encoding, unsupported TE, invalid CL and chunk sizes
- Bare LF and malformed headers (NUL, obs-fold), invalid UTF-8 text bodies
- Truncated fixed-length bodies, over-budget declared and chunked bodies
- Unsupported HTTP version and a smuggled second request attempt
- No successful responses to malformed requests, no persisted mutation,
  no server poisoning, and subsequent healthy GET/health responses

Each negative case must receive an explicit transport denial (400, 408, 413,
414, 422, 431 or 505), or a clean closed connection with no response. A 503
capacity rejection is inconclusive and retried. A 500, 409, 200, a smuggled
second successful response or a silent hang is a failure. Time, response bytes
and traffic are bounded; test traffic is loopback only.

This is deliberately a **strict desired acceptance contract**, not a statement
that the pinned implementation currently passes. If a case fails, triage the
HTTP parser/compiler rather than relaxing the expectation or marking it
unverified. A transport close without a response is accepted as fail-closed,
but must be separately classified in receipts so a future policy can insist on
a specific status code.

## Evidence required for promotion

Record demo commit SHA, compiler SOURCE_REF and dependency submodule SHAs;
execution mode (JVM interpreter, native runtime image, real guest AOT), OS,
runner ID, test command, exit status, log artifact, and non-zero executed steps.
Host-only `test_http_adversarial_host.py` proves the harness classifier only.
The private compiler cannot be fetched by the public demo repo's ordinary
cross-repository `GITHUB_TOKEN`; use only an authorized checkout or an
approved secure runner. Do not export private source into a test organization
without explicit authorization and verified provenance.
