# Experiment write-ups

An experiment is not finished when its command exits. It is finished when its
evidence has been reduced into a bounded claim and a code decision.

Every repository-local directory under `experiments/` must keep a
`LEARNINGS.md`. Add one entry for each controlled experiment. Supporting probes
may share a shorter section when they validate the same adapter boundary rather
than answer independent questions. Local experiment sources remain outside the
`0.1.0rc2` distribution; this standard is the shipped description of the
maintainer workflow.

## Required distinction

Keep these three outcomes separate:

1. **The subject worked.** The external harness performed the intended action.
2. **The measurement worked.** The adapter and oracle could distinguish the
   intended result from known false-success cases.
3. **The result should change shared code.** The same requirement is general
   enough to belong outside the subject-specific experiment.

A clean run proves neither the second nor the third outcome automatically.

## Cross-harness promotion rule

Pi is the reference integration, not the schema for every agent. Record each
finding as one of:

- **change now** — a defect in already-shared Workbench behavior;
- **keep local** — necessary for this harness or workload, but not established
  as a cross-harness contract;
- **candidate for reuse** — a shape another adapter should deliberately test;
- **promote after repetition** — extract into shared code only after a second
  independent harness needs the same boundary;
- **no code change** — useful evidence that confirms the current design.

This prevents Pi event names, provider assumptions, and lifecycle details from
leaking into the Workbench core merely because Pi was integrated first.

## Required evidence

Each entry must name:

- the question and falsifiable expectation;
- the subject, pinned runtime, treatment, and control;
- the run IDs or exact verification command when durable run IDs do not exist;
- what happened, including negative or unexpected results;
- what the evidence does **not** establish;
- the code consequence and its status;
- the next experiment that could overturn or strengthen the conclusion.

Do not use model self-report as the outcome oracle. Prefer tool events, exit
states, exact bytes, manifests, external tests, and durable effects.

## Entry template

```markdown
## E00 — Short experiment name

**Question.** What exact uncertainty is this testing?

**Expectation.** What result would support or reject the hypothesis?

**Setup.** Subject/version, control, treatment, frozen inputs, and oracle.

**Evidence.** Run IDs and verification commands.

**Result.** What happened, without interpretation beyond the evidence.

**Learned.** The smallest justified conclusion.

**Code consequence.** `change now`, `keep local`, `candidate for reuse`,
`promote after repetition`, or `no code change`, followed by the concrete
file/interface decision.

**Limits.** What this experiment did not test.

**Next.** The next discriminating experiment.
```

When code changes because of an experiment, link the relevant file or commit.
When it does not change, say why; avoiding an unjustified abstraction is also
a code decision.
