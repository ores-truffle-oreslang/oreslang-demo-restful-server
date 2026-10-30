# Demo validation gates

Status snapshot: 2026-10-29. This is an evidence checklist, not a passing test report.

## Scope
REST route demo.

## Tests required before promotion
Run scripts/test.sh, loopback curl-10 and separate native AOT/hybrid distribution checks.

Record exact demo and compiler commit SHAs, compiler executable SHA-256, backend/target, executed commands, exit/stdout/stderr, CI runner and step-level results. Unrun, skipped and zero-step runs are **NOT_EXECUTED**, never test passes. Include positive/negative scenarios, bounded-resource behavior and cancellation/teardown where applicable.

## Limitations
Single active request and local file persistence; not production concurrent storage.

No production readiness or cross-backend parity is asserted by these documentation changes. Keep native behavior in Oreslang guest source where promised; distinguish host checks from executed guest checks. Do not mirror private Git trees to another organization without explicit approved destination, secure policy and exact-tree provenance.
