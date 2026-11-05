# Route publication: source-host security and verification contract

This document describes the **current host generator**
[`scripts/generate-routes.py`](../scripts/generate-routes.py). It is a source
publication contract, **not** a certificate that the Oreslang/AttoLang compiler,
Spin HTTP actor, or native guest executed successfully.

## Trust boundaries at generation time

| Input or output | Current admission rule | Observable failure boundary |
| --- | --- | --- |
| `port` | Exactly the built-in Python `int` type, inclusive range `1..65535`; booleans, floats, strings and `int` subclasses do not qualify | `ValueError` before route discovery or generated-file publication |
| Route root | `src/routes/rest` must be a real directory, not a symlink | `ValueError` before traversing route files |
| Route leaves | Recursive `*.ores` candidates are sorted; symlinked route leaves and resolved paths escaping the root are rejected | `ValueError` before emitting the route table |
| HTTP method | Lowercase filename stem exactly one of `get`, `post`, `put`, `patch`, `delete`, `head`, `options` | Unsupported stems are rejected; uppercase aliases do not silently become routes |
| URL path | Every path component below `rest/` must match `[a-zA-Z0-9_-]+` | No dynamic, whitespace, percent-encoded, dotted, empty or Unicode segments are admitted as literal routes |
| Empty discovery | At least one route must exist | Empty route trees produce `ValueError` |
| Generated directory | `src/generated` may be created as a real directory; a symlink at that path is rejected | The generator will not follow a redirected output directory |
| Generated leaves | Each file is staged in the destination directory and installed with `os.replace`; temporary file cleanup runs even after a replace failure | Readers do not observe a partially written **individual file**; a stale generated-leaf symlink does not redirect writes to its target |
| Config values | The data file path is produced from resolved `data_dir` and emitted as a JSON-escaped string literal | Quotes, backslashes and controls are encoded rather than interpolated as source syntax |

## Publication ordering and what is *not* atomic

The generator discovers and validates routes before constructing the generated
source, then writes `routes.ores` and `config.ores`. Each individual replacement
is atomic at the directory-entry level, but **the pair is not an atomic
transaction**. Do not infer that concurrent readers can never observe different
generations of the two files. A later I/O failure can occur after the first
replacement. Existing output is preserved when discovery fails; that is a
narrower guarantee than arbitrary-failure transactional rollback.

The generated table uses `http_routing.literal(...)` for admitted components,
with deterministic route order and separate `route_N` module names. This is
**static route emission**, not a request-time disk path resolver.

## Executable verification

From a clean checkout at a known PR-head Git SHA:

```sh
python3 -m unittest discover -s tests -p test_route_publication.py -v
bash scripts/test-guards.sh
```

The source-owned `.github/workflows/route-publication.yml` performs an
exact-head checkout check and executes shell syntax, trust guards, bounded
embedded archive tests, the route publication unittest suite and
capability-input guard checks. A green job with executed steps is evidence for
**those host assertions at that exact commit only**.

A failed workflow with no executed steps is **NOT_EXECUTED**, not a successful
negative test. A structural-only source hygiene check is not semantic
compilation. Even successful generator and guard checks do **not** establish
compiler-backed Oreslang/AttoLang execution, HTTP networking correctness,
native-image packaging, cross-backend parity or production readiness.

## Scope of future changes

A change that admits dynamic paths, changes generation ordering, widens route
stems, introduces symlink following, or promises transactional two-file
publication must update the tests and this contract together. Do not work
around a host-policy failure by weakening CI source hygiene or substituting a
mock compiler for actual guest execution.
