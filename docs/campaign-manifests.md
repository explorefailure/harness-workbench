# Campaign manifests

Campaigns preserve their own evidence separately from ordinary runs. This is
the reference for the manifests written by `steady`, `effects`, `interrupt`,
and `sensitivity`; use [`measuring.md`](measuring.md) for what the campaigns
mean and [`measuring-your-own-code.md`](measuring-your-own-code.md) for when to
run them.

## Location and store boundary

Each command writes canonical JSON at:

```
<campaign store>/<campaign_id>/campaign.json
```

The store selectors are global options, so they precede the command:

| command | default store | selector | environment default | schema |
|---|---|---|---|---|
| `steady` | `./steadies` | `--steadies` | `HWB_STEADIES` | `hwbsteady/v0.1` |
| `effects` | `./effects` | `--effects-store` | `HWB_EFFECTS` | `hwbeffects/v0.1` |
| `interrupt` | `./interrupts` | `--interrupts` | `HWB_INTERRUPTS` | `hwbinterrupt/v0.1` |
| `sensitivity` | `./sensitivity` | `--sensitivity` | `HWB_SENSITIVITY` | `hwbsensitivity/v0.1` |

For example, `hwb --steadies evidence/steadies steady spec.json` writes
`evidence/steadies/<campaign_id>/campaign.json`. A campaign id is a UTC
timestamp plus a random suffix.

The resolved run store and campaign store must be disjoint. Equality, a
campaign store nested under the run store, a run store nested under the
campaign store, and equivalent paths reached through symlinks are all rejected.
The check resolves absolute real paths before either kind of evidence is
created. The sibling defaults satisfy the rule. `effects` additionally refuses
a watched root that overlaps either store, so the instrument's own writes
cannot enter its subject envelope.

Campaign manifests are not run records. They have no `integrity.json` and make
no claim that their bytes are sealed against later edits. Their run ids point
to ordinary evidence under the run store selected by `--root`/`HWB_RUNS`.

## `steady`

Location: `steadies/<campaign_id>/campaign.json` by default. The campaign
directory contains the manifest; the repeated runs remain ordinary run
directories.

| field | meaning |
|---|---|
| `schema` / `campaign_id` | `hwbsteady/v0.1` and the directory id |
| `base_spec` / `base_spec_digest` | absolute starting spec path and its digest |
| `features_root` | resolved feature root pinned at campaign start |
| `repeats_requested` / `run_ids` | requested repeat count and successfully created runs |
| `allowance` | sorted exact axes permitted to move |
| `comparisons` | baseline-versus-repeat rows described below |
| `moving_axes` / `unallowed_axes` | campaign-wide unions, including allowed motion in the former |
| `verdict` / `setup_error` | final classification and any execution-stage setup failure |

Each `comparisons[]` row carries `run_a`, `run_b`, its copied `allowance`,
`harness_differences`, `output_differences`, normalized `moving_axes`,
`unallowed_axes`, `detail`, and `verdict`.

Verdicts are `stable`, `UNSTABLE`, `uninterpretable`, and `setup_error`.
`stable` means no unallowed axis moved; allowed motion is still recorded.
`UNSTABLE` means at least one unallowed axis moved. A comparison refusal or
unavailable stored output makes the result `uninterpretable`. An execution
failure after the campaign directory exists yields `setup_error`; invalid
preconditions such as fewer than two repeats produce no manifest.

Observation is limited to the same harness projection and stored-output axis
used by `diff`, plus resolved feature-source digests. It is not a statistical
rate, majority vote, warm-up protocol, or proof that dependencies omitted from
the spec stayed fixed.

## `effects`

Location: `effects/<campaign_id>/campaign.json` by default. Its one `run_id`
names the ordinary subject run, when execution reached that point.

| field | meaning |
|---|---|
| `schema` / `campaign_id` | `hwbeffects/v0.1` and the directory id |
| `base_spec` / `base_spec_digest` | absolute starting spec path and its digest |
| `run_id` | subject run id, or `null` when none was produced |
| `watched_roots` | normalized spec-relative `path` entries |
| `allowed_paths` | each allowed `path` and the watched root that owns it |
| `sensor` | sensor `name`, explicit `observed` and `unobserved` classes, and `unobserved_special_paths` |
| `changes` | every endpoint difference; `allowed_changes` and `breaches` are its two projections |
| `verdict` | `within_envelope`, `BREACH`, `uninterpretable`, `setup_error`, or `instrument_error` |
| `setup_error` / `instrument_error` | subject-execution failure versus snapshot failure |

A `changes[]` row contains `path`, `change`, `allowed`, and `before`/`after`
fingerprints. `change` is `added`, `removed`, `type_changed`,
`content_changed`, `mode_changed`, or the fallback `changed`. A missing
endpoint is `null`. Fingerprints always carry `type` and four-digit `mode`;
regular files add `bytes` and `digest`, symlinks add `target` and a digest of
that target without following it, and special nodes carry
`content_observed: false`.

`within_envelope` means every observed endpoint change was allowed. It does
not mean globally clean. Special paths make the scoped result
`uninterpretable`; an unallowed change is a `BREACH`; setup and sensor failures
remain separate verdicts.

The sensor is exactly `portable-endpoint-tree-snapshot/0.1`: two snapshots,
before feature resolution and after run close. It observes endpoint-visible
creation/removal, regular-file size and content, link targets, types, and
permission modes only inside explicit watched roots. It does not see
create/delete pairs between snapshots, reads or failed writes, timestamps,
ownership, extended attributes, ACLs, locks, processes, IPC, network activity,
or anything outside those roots. It does not attribute a change to a feature.

## `interrupt`

Location: `interrupts/<campaign_id>/campaign.json` by default. The directory
also contains `00-before_run_directory/` through `07-integrity_written/`; a
checkpoint that was reached preserves its atomic `reached.json` coordination
marker there. Run evidence stays under the run store.

| field | meaning |
|---|---|
| `schema` / `campaign_id` | `hwbinterrupt/v0.1` and the directory id |
| `base_spec` / `base_spec_digest` | absolute starting spec path and its digest |
| `runs_root` | absolute run-store path used by the children |
| `checkpoint_protocol` | `atomic-marker-then-direct-child-terminate/0.1` |
| `timeout_seconds` | per-child checkpoint/control bound |
| `state_oracle` | definitions of `absent`, `incomplete`, `recoverable`, and `complete` |
| `checkpoints` | eight checkpoint children plus `uninterrupted_control` |
| `violations` | flattened `"<checkpoint>: <violation>"` strings |
| `unobserved` | explicit limits of the bounded campaign |
| `verdict` | `passed`, `VIOLATIONS`, or `setup_error` |

Each checkpoint row records `checkpoint`, whether it is a positive or negative
`control`, `expected_state`, `observed_state`, `run_path`,
`observed_inventory`, `state_reasons`, `violations`, bounded child stdout and
stderr, and `child`. The child object records `result`, `returncode`, `signal`,
`terminate_requested`, and whether termination escalated to a kill.

`passed` means every checkpoint and the uninterrupted control matched its
expected state. A published but wrongly classified boundary produces
`VIOLATIONS`; a missing/unreadable marker or failed uninterrupted control is a
`setup_error`.

This is nine child executions: eight named closed-file boundaries and one
uninterrupted control. It terminates only the direct runner child after an
atomic marker. It does not observe intervals between checkpoints, arbitrary
instruction points, power/kernel/cache loss, `fsync` durability, descendants,
network/IPC/lock cleanup, or resume/repair/quarantine/deletion behavior.

## `sensitivity`

Location: `sensitivity/<campaign_id>/campaign.json` by default. Each
`<campaign_id>/<probe>/` directory is isolated scratch evidence: copied runs
for record probes, or a fresh workload for replay. The subject run is never
modified.

| field | meaning |
|---|---|
| `schema` / `campaign_id` | `hwbsensitivity/v0.1` and the directory id |
| `subject_run` / `runs_root` | subject id and absolute source run store |
| `public_verdict_engines` | sorted engine universe derived from public command metadata |
| `probes` | one row per registered probe, followed by generated coverage rows for engines with no probe |

A `probes[]` row contains `probe`, `checker`, `control`, `why`, `verdict`, and
`detail`. Its verdict is `detected`, `MISSED`, `errored`, or `UNPROBED`.
`UNPROBED` is generated whenever a public verdict engine has no known-red
probe; it is a coverage failure, not a skipped pass.

There is deliberately no top-level verdict field. The CLI derives the result:
the positive control must be `detected`, and any `MISSED`, `errored`, or
`UNPROBED` row exits nonzero. `checker coverage` counts engines with at least
one probe; it does not claim that every probe detected its violation.

Most record probes call the public checker on copies. Campaign-oriented probes
may enter at the smallest production observation/classification boundary that
decides the verdict; `detail` identifies that boundary. Such a row proves the
named path rejects one constructed known-red case, not that sensitivity ran
the campaign's full acquisition protocol, nor that the checker detects every
violation class. The current replay-output probe is intentionally `MISSED`, so
a nonzero sensitivity exit is expected until that separate runtime defect is
fixed.
