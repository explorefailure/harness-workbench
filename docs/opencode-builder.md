# OpenCode builder integration candidate

`hwb-opencode` creates an owned adapter workspace, checks a pinned OpenCode
binary, captures CLI JSON events, and independently evaluates a reference repair.
This is an unreleased integration candidate. The cross-harness envelope remains
experimental and is not extended or promoted by this integration.

## First run

Install this Workbench candidate in a Python 3.11+ virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
hwb-opencode init ./my-opencode-workspace
cd my-opencode-workspace
python3 install_runtime.py
hwb-opencode doctor . --opencode ./runtime/opencode
hwb-opencode demo . --opencode ./runtime/opencode --output repair.result.json
```

Initialization performs no downloads and refuses existing destinations, including
symlinks. The explicit installer downloads OpenCode 1.19.5 from official GitHub
release assets, checks archive and binary SHA-256, and writes only NEW runtime/.
It extracts only the expected executable. doctor checks binary identity BEFORE
executing a bounded version probe. Your global OpenCode install is untouched.

The default demo starts a bounded scripted OpenAI-compatible server on an
ephemeral loopback port. It drives actual read, bash, edit, bash calls. No API
account or model inference is involved. Independent Python tests fail before the
repair and pass afterward; fixture manifests must show only slugger.py changed.
The original test file must retain its bytes and mode. Native tool status is
kept separately from bash metadata.exit: OpenCode can mark a tool completed even
when the shell command exits nonzero.

## Run a local model

Edit provider in adapter_config.json:

```json
{
  "mode": "local",
  "base_url": "http://127.0.0.1:11434/v1",
  "model": "YOUR_INSTALLED_MODEL"
}
```

```sh
hwb-opencode demo . --opencode ./runtime/opencode --timeout 360 --output local.result.json
```

Use an installed model with tool support and sufficient SERVER context for
OpenCode's instructions and tool definitions. The adapter advertises a conservative
32K context/4K output in its generated config; that does not reconfigure the
inference server. Ensure the server's actual context is at least 32K. With Ollama,
use an existing model profile with an appropriate num_ctx. No model download,
server restart, or global model reconfiguration is performed by the adapter.

Local mode supports unauthenticated OpenAI-compatible endpoints. A fixed dummy
Authorization value is used; saved provider accounts and ambient credential
variables are not passed. base_url rejects embedded credentials and query strings.
You explicitly choose and trust the endpoint. The adapter does not enforce network
isolation or prevent a tool reading arbitrary absolute paths. This is not a
hostile-code sandbox. Do not place secrets in fixtures or tasks.

The reference outcome check also applies to live-model demos: unchanged tests,
only implementation changed, native test failure followed by pass, independent
before/after tests, complete stream and clean bounded process termination. Local
models may use bash to inspect/edit; the fixed native read/edit sequence is
required only for the scripted control. A passing repair is one observation,
not a general claim about model quality or deterministic live-model replay.

## Commands, evidence and custom workloads

- `hwb-opencode init DIR`: NEW owned workspace with editable sources and pin.
- `hwb-opencode doctor DIR`: runtime identity and config/provenance check.
- `hwb-opencode demo DIR`: reference coding-repair outcome check.
- `hwb-opencode run CONFIG`: capture validity only; no general task oracle.

All execution commands accept --opencode LAUNCHER, --timeout POSITIVE_SECONDS,
--workspace-parent EXISTING_DIR and --output NEW_FILE. The launcher can be on PATH
or an explicit path. Output files are created owner-only and never overwritten.
Exit 0 means the selected check passed; 1 means a measured capture/outcome failed;
2 means configuration/identity/launch failed. Missing runtime and malformed
configuration produce structured errors. Timeouts and byte bounds are evidence,
not fabricated successful completion.

Each run uses fresh HOME, XDG config/data/cache/state and TMPDIR. Project config
and parent instruction discovery, default/external plugins, model catalog fetch,
autoupdate, sharing, snapshots, LSP, formatters and file watching are disabled.
Read/edit/bash are allowed and other tools denied. This is a deliberately narrow
integration; inherited MCP tools, resumed sessions, interactive permission prompts,
custom plugins and authenticated cloud providers are outside its supported scope.
Managed host OpenCode configuration/preferences are refused because HOME/XDG
isolation cannot isolate them. Upstream detached plugin dependency installation
is set to npm offline mode with empty user/global npm configuration and install
scripts disabled; an unused background dependency warning may appear.
The upstream CLI emits completed tool observations, not full start/end execution
pairs; this adapter does not claim otherwise.

Retained roots contain raw stdout.jsonl, stderr.txt, generated runtime.json,
result.json and fixture snapshots/manifests. Scripted runs retain provider.json
with the actual provider-visible conversation. Event summaries retain native tool
IDs, inputs, outputs, status and exit metadata. Raw output digests are never
replaced by a normalized comparison hash. There is no claim of byte-identical
replay across timestamps, session IDs or model samples. Run roots persist until
you remove them; review paths and task data before sharing.

Copy adapter_config.json for a custom workload and declare the new file itself,
pin, task, adapter.py, normalizer.py, scripted_provider.py and every fixture file
in inputs. Paths must be relative within the owned workspace; links and special
fixture files are refused. The scripted provider is specific to the reference
repair; use local mode for custom workloads and supply your own outcome oracle.

For an outer sealed record put runtime/ on PATH, run `hwb run coding.json`, then
`hwb verify RUN_ID`. Inspect the inner step's exit and outcome as well: outer
recorder success is not task success. freeze/receipt bind the declared source
bytes, including the runtime pin. Start a new workspace when inputs change;
do not reset a baseline to hide drift.

## Runtime and validation scope

The pin binds official OpenCode 1.19.5 source tag to commit
810c0eba1a7348036f8bdf61e6632d0c36c6876c and contains archive/binary digests for
Linux x64/ARM64 and macOS x64/ARM64. Runtime byte identity is checked at each
invocation. This is not an upstream vulnerability audit or a proof of containment.
Linux x64 is exercised locally; successful hosted runs on the exact candidate
are required before making macOS or ARM compatibility claims. The workflow runs
the actual scripted repair, including negative outcome checks, across configured
Linux/macOS and Python versions. A configured matrix is not a passing result.

Official interface references: [CLI](https://opencode.ai/docs/cli/),
[configuration](https://opencode.ai/docs/config/),
[providers](https://opencode.ai/docs/providers/), and
[pinned source](https://github.com/anomalyco/opencode/tree/v1.19.5).
