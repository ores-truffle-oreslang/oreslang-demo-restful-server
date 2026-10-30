# Change summary

Describe changed behavior, affected Oreslang language/runtime surfaces, and why the change is necessary. Link relevant upstream/compiler PRs.

## Compatibility and safety

- [ ] Native `.ores` semantics remain the source of truth for backend-neutral behavior, or the host-only boundary is explained.
- [ ] Java/Graal, LLVM, .NET, Wasm, Cranelift, Forge and other relevant backend implications are stated; unsupported features fail closed.
- [ ] Relevant actor isolation, non-reentrancy, async cancellation, borrow lifetimes, and bounded resource/real-time constraints are tested.
- [ ] No implicit conversion, user-defined operator overloading, or Future/Option auto-flattening is assumed.
- [ ] Not-applicable items are identified, not falsely marked as verified.

## Verification evidence

- **Exact PR head SHA / root Git tree:**
- **Executed commands / workflow run and job IDs:**
- **Nonzero runner steps and pass/fail/skip counts:**
- **Compiled `.ores` guest evidence vs host-only evidence:**
- **Backend differences, rejected cases, and follow-up issues:**

Queued, skipped, or zero-step jobs are not executed CI. Host-only tests are not proof of compiled guest behavior. Disclose any gaps.

## Review and source handling

- [ ] Existing CODEOWNERS, required reviews, branch protections, and publication gates are respected; this change does not bypass them.
- [ ] Any private-source external CI transfer has separate explicit source/destination authorization, exact-tree verification, and no cross-org checkout credentials. Otherwise, no private-source export occurs.
- [ ] Stacked PR dependencies and post-merge checks are described as applicable.
