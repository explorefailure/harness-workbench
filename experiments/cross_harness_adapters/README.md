# Cross-harness adapter experiment (research record)

The runnable tree now ships with the package at
`src/harness_workbench/subjects/`, and `hwb subjects --into <dir>` copies it
out. This directory keeps the research record only — `LEARNINGS.md` — so that
the code has exactly one home and cannot drift between two copies.

To run anything described in `LEARNINGS.md`:

```sh
hwb subjects --into ./subjects
cd subjects
python3 -m unittest test_experiment.py
```

The contract those runs are judged against is
[`SHARED_ADAPTER_CONTRACT.md`](../../src/harness_workbench/subjects/SHARED_ADAPTER_CONTRACT.md).

## Deferred: the rest of the intervention matrix

Five subjects currently run two **observational** workloads — an exact write
and a red → edit → green repair. Both ask "did the subject do the task," which
measures the subject, not a control.

The interesting experiments invert a control the way `efficacy` does for
features. `experiments/pi_coding_agent/` already has nine such families built
against Pi's extension API; only the first has been generalized. They do not
port as implementations — each harness intercepts through a different
mechanism (Pi extensions, Claude and Codex hook JSON, Hermes shell hooks,
DeepSeek plugins) — so each cell needs interceptor code written in that
harness's own form.

| Family | Pi spec | Ported | Question |
|---|---|---|---|
| tool guard | `allow` / `block` | planned first | does a guard actually stop the call? |
| interceptor failure | `audit_first` / `throw_first` | no | when an interceptor crashes, does the call proceed? |
| result-stage failure | `result_audit_first` / `result_throw_first` | no | same, after the tool ran |
| policy order | `allow_first` / `block_first` | no | when two policies conflict, who wins? |
| composition order | `guard_first` / `mutate_first` | no | does interceptor order change the effect? |
| result rewrite | `result_mask_first` / `result_restore_first` | no | can a result be masked, then restored? |
| branch honesty | `branch_honest` / `branch_falsified` | no | is a rewritten result detectable as a lie? |
| failure honesty | `failure_honest` / `failure_falsified` | no | is a rewritten failure detectable? |
| plan vs act | `plan_mode` / `action_mode` | no | does plan mode actually withhold effects? |

Capability limits already known, from `SHARED_ADAPTER_CONTRACT.md`:

- **deny a call** — all five, so the guard family is the portable one;
- **rewrite tool input** — Claude and Pi yes, Codex unverified, Hermes and
  DeepSeek no;
- **rewrite tool result** — Claude, Pi, and DeepSeek;
- **interceptor ordering** — needs two interceptors with defined order;
  documented for Pi, Hermes, and DeepSeek, unclear for Claude and Codex.

DeepSeek's surface is now probed and is richer than assumed: its `dsh-tools`
pipeline runs `tools/pre-execute` (allow/deny) → guards → `tools/execute`
(around-dispatch) → `tools/post-execute` (inspect/replace result) →
`tools/result` (observe-only). `ToolExecution` is immutable and a wrapper may
replace only the operational signal, so it can deny and rewrite results but
cannot rewrite inputs — the mirror image of Codex.

The holes are findings, not gaps to paper over: a harness that cannot express
an intervention is evidence about that harness's interception surface.

Highest value first, because the answers are already known to differ: the
**interceptor-failure** family. Hermes shell hooks fail *open* while its
plugin-approve and ACP edit-approval paths fail *closed* (see E-notes). "We
crashed the policy hook on five agent harnesses; here is which ones let the
write through" is a safety result, not a capability comparison.

**Open before that work starts:** DeepSeek's interception surface is the one
unknown. It needs a probe before it can be committed to any intervention
family.
