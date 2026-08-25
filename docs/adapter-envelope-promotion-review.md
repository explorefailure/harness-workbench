# Adapter envelope API/schema promotion review

Decision date: 2026-08-25 UTC.

Decision: **defer promotion of `cross-harness-adapter-run/v0.1` into the
supported Workbench API for `0.1.0rc2`.** Keep the envelope, its normalizers,
and its comparator in the materialized experimental subject tree. This closes
the review gate with a deliberate non-promotion decision; it is not an
unresolved approval.

## Scope

The review asked whether the complete cross-harness adapter envelope should
become a public Workbench schema or importable core module. It did not reopen
the earlier extraction of generic process-capture and canonicalization
primitives. Those concerns already have two independent consumers and are the
supported `harness_workbench.capture` and `harness_workbench.canon` modules.

The candidate reviewed here is the envelope defined in
[`SHARED_ADAPTER_CONTRACT.md`](../src/harness_workbench/subjects/SHARED_ADAPTER_CONTRACT.md),
including the strict comparison implemented by `subjects/compare.py`. The
current-source five-subject write and repair comparisons both pass that
contract. Those results establish that the experiment can compare its sealed
records; they do not establish that its schema is a suitable compatibility
promise for unrelated Workbench users.

## Findings

| Public-surface question | Finding | Consequence |
| --- | --- | --- |
| Is the vocabulary vendor-neutral? | No. The comparator requires exactly Claude, Codex, DeepSeek, Hermes, and Pi and validates subject-specific identity, launcher, model-profile, hook, and terminal rules. | Promotion would make five current third-party integrations part of Workbench's compatibility surface. |
| Does the schema follow core evolution rules? | No. The comparator requires exact field sets throughout. Core record conformance deliberately ignores unknown keys so additive changes remain compatible. | Promoting the validator unchanged would introduce a conflicting schema-evolution policy. |
| Is validation independent of the experiment environment? | No. Comparison binds the live pin declarations, active model profile, workload set, and current `capture`/`canon` apparatus. | A public validator could change meaning when experiment data changes, even if its caller's record does not. |
| Are the semantics stable across upstream releases? | Not yet. The exact Claude pin changed its native tool-result shape, DeepSeek is a breaking-change developer preview, and Hermes exposes a process-only terminal boundary with nondeterministic retained execution evidence. | The normalizers need freedom to change without a core deprecation cycle. |
| Is there a second independent consumer of the whole envelope? | No. Producers and the strict comparator are all part of the same subject experiment. | There is no demonstrated cross-project API need beyond the generic primitives already extracted. |
| Can a smaller reusable boundary be named now? | Yes, and it already exists: bounded capture, canonical digests, manifests, redaction, containment, and JSONL decoding. | No additional core module is justified for this release candidate. |

The closed-world rules are appropriate for sealed experiment evidence. Exact
subject membership and exact maps prevent a record from silently acquiring a
new interpretation inside a comparison. The issue is not that those rules are
wrong; it is that they serve a different compatibility model from a supported,
additively evolving library API.

## Release consequence

No adapter-envelope module is added under `src/harness_workbench/`, no new
names are added to a public module's `__all__`, and the public-library manifest
remains limited to the already reviewed modules. The following remain
experiment-local:

- `cross-harness-adapter-run/v0.1` and the five-subject comparator;
- every harness normalizer, command builder, pin, identity, and model profile;
- guard receipts, workload outcomes, and workload-specific oracles; and
- any semantic projection of adapter lifecycle or steadiness evidence.

The strict Hermes `steady v0.1` result remains unchanged: a future semantic
projection would be a separately versioned product that retains raw evidence,
not an allowance that discards the complete adapter envelope.

## Conditions for another review

Reconsider promotion only after the work produces:

1. a vendor-neutral envelope independent of an exact subject roster, current
   harness versions, commands, model profiles, and workloads;
2. an explicit unknown-field and additive-evolution policy compatible with
   Workbench's public record contract;
3. a pure producer/validator boundary whose result does not depend on reading
   the live subject tree or active model profile;
4. typed public entry points with failure semantics and a versioning policy;
5. at least one independent consumer outside this five-subject experiment;
   and
6. public-module routing, `__all__` coverage, exported-name tests, migration
   notes, and release-conformance evidence.

Until then, the materialized subject tree is the correct boundary: exact
adapter bytes are frozen as experiment inputs, while Workbench core remains
independent of any model, provider, or agent framework.
