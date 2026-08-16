# Shared external-harness adapter contract candidate

Status: experiment-local candidate, schema `cross-harness-adapter-run/v0.1`.
It is not a Harness Workbench core API yet.

## Purpose

An adapter turns one bounded external-agent invocation into evidence that a
workload-specific oracle can judge. It does not decide whether a coding task,
policy, or experiment succeeded. The same envelope must represent a clean
success, a tool failure, a timeout after a successful effect, and malformed
subject evidence without confusing those outcomes.

The contract specifies common evidence responsibilities. It does not require
Claude, Codex, DeepSeek Harness, Hermes, and Pi to emit the same native events.

## Required envelope

Every `cross-harness-adapter-run/v0.1` object contains:

| Field | Required meaning |
| --- | --- |
| `subject` | Harness name and version, executable or install digest, declared model, and the strongest available model identity. Hosted model labels are declarations; a local model content digest is stronger. |
| `request` | Digest of the exact prompt plus a digest map for every consumed experiment input. |
| `capabilities` | Explicit booleans or typed values for native events, hook events, native terminal events, tool correlation, result status, and model-identity strength. Unsupported features are not synthesized. |
| `invocation` | Credential-safe argv, working directory marker, timeout, and credential-source class. |
| `isolation` | Disposable-workspace claim, ambient-config policy, and network scope. This is disclosure, not proof of containment. |
| `capture` | Credential-scrubbed stdout, stderr, and named sidecar bytes up to declared positive limits, with source/stored byte counts, stored SHA-256 digests, redaction counts, overflow state, process return code, and termination reason. |
| `lifecycle` | Acquisition method, completeness class, ordered native event types, normalized tool attempts, and the strongest observed terminal boundary. |
| `workspace` | Exact before and after manifests collected outside the subject. |
| `verdict` | Whether the adapter could establish its declared identity, capture, and lifecycle invariants. |
| `outcome` | A separate workload-specific durable-effect verdict. |

Workbench `freeze` and `receipt` bind the declared inputs a second time. A
comparison is valid only when the Workbench maps agree with each other and with
the adapter's independent `request.input_digests` map.

## Normalized tool attempt

Each available attempt has:

```json
{
  "call_id": "subject correlation id or null",
  "tool_name": "subject tool family",
  "effect_kind": "read | write | command | other",
  "operation": "recognized semantic operation or null",
  "operation_exit_code": "optional child exit status when recoverable",
  "arguments_sha256": "sha256:...",
  "arguments_stage": "subject_proposal | subject_event | pre_tool_call_hook",
  "reported_error": false,
  "result_stage": "subject_reported | hook_observer",
  "acquisition": "native_jsonl | native_persisted_jsonl | shell_hook"
}
```

The stage labels are mandatory because the same digest does not have the same
meaning everywhere. A pre-hook payload is a proposal and might not be the
effective executed arguments. A native file-change event is a subject report,
not an external proof that the bytes landed. The after-manifest and outcome
oracle supply the independent effect evidence.

Normalizers reject duplicate lifecycle terminals, duplicate tool identifiers,
orphaned native tool completions, incomplete hook pairs, and post-before-pre
hook ordering. A reported tool error remains a valid measured attempt; it does
not make the adapter structurally invalid by itself.

`reported_error` preserves the harness's own tool-result status. It is not
silently rewritten from a shell exit code. DeepSeek's bash tool, for example,
can report `isError: false` while its first-party result text ends with
`[exit code: 1]`; the adapter projects that separate fact as
`operation_exit_code: 1`, and workload oracles may use both fields.

## Capability observations

| Harness | Acquisition | Strongest terminal evidence | Tool evidence | Observed limitation |
| --- | --- | --- | --- | --- |
| Pi `0.84.1` | Native JSON stream | Native session lifecycle | Correlated native calls and results | Pi-specific extension and provider events must remain local. |
| Claude Code `2.1.233` | Native `stream-json` | Native `result` event | Correlated `tool_use` / `tool_result` | Hosted model identity is a label, not a content digest. |
| Codex CLI `0.144.1` | Native `--json` JSONL | Native `turn.completed` | Started/completed item pairs | A valid lifecycle can still contain failed tool attempts or the wrong durable bytes. |
| DeepSeek Harness `0.1.0-rc.6` | Native persisted JSONL plus process | Native `turn/end` | Correlated `tool/call` / `tool/result`, plus projected bash exit marker | The supported headless stdout contains final text only; the developer-preview session format has no compatibility promise, and `tool/result.isError` is not a child-process exit verdict. |
| Hermes Agent `0.16.0` | Shell hooks plus stdout/stderr | Process exit only | Correlated pre/post hook pairs when a call occurs | The final repair run terminated cleanly after one read but never attempted red/edit/green; earlier probes also timed out or proposed outside-workspace paths. |

Claude documents noninteractive `-p` operation and streamed JSON output, while
its hooks expose lifecycle and tool boundaries:
<https://code.claude.com/docs/en/headless> and
<https://code.claude.com/docs/en/hooks>.

Codex documents `codex exec` as its noninteractive surface, with JSONL output,
ephemeral runs, ignored config/rules, and selectable sandbox policy:
<https://developers.openai.com/codex/cli/reference>.

DeepSeek documents `dsh --profile headless` as a one-task persisted session that
prints final assistant text and exits. The official repository labels the
harness a developer preview with breaking changes:
<https://github.com/deepseek-ai/deepseek-harness>.

Hermes documents shell hooks as JSON pre/post observers that may block a call;
the CLI process remains the terminal boundary when no native event stream is
available:
<https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/hooks.md>.

## Deliberate non-goals

- No universal assistant-message or token-usage schema.
- No claim that `safe-mode`, `workspace-write`, or a disposable directory is a
  security boundary.
- No assumption that process exit zero proves the requested task succeeded.
- No assumption that a durable effect proves the harness finished cleanly.
- No extraction into Workbench core until Hermes completes the live workload
  matrix and the remaining containment/redaction edge cases are tested.

## Promotion gate

Promote this shape only after:

1. the four sealed discovery records compare successfully at the contract
   layer, even when a subject or outcome verdict is negative;
2. mutation tests make each normalizer reject false-success lifecycle records;
3. each harness completes at least one second workload through the same adapter;
4. timeout and cancellation record partial effects without claiming a native
   terminal event; and
5. credential redaction and bounded raw-capture limits are implemented.

Current status: gates 1, 2, 4, and 5 have passing evidence. Final four-subject
write and repair comparisons both return `contract_passed: true`. Gate 3 is
complete for Claude and Codex. DeepSeek completed one earlier repair probe but
did not repeat it in two final-source samples; Hermes produced valid adapter
evidence on the repair workload but stopped after one read. Promotion remains
blocked.
