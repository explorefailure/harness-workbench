# Owned OpenCode builder workspace

This integration pins OpenCode 1.19.5. Install Workbench first, then:

```sh
python3 install_runtime.py
hwb-opencode doctor . --opencode ./runtime/opencode
hwb-opencode demo . --opencode ./runtime/opencode --output repair.result.json
```

The explicit installer downloads an official release asset, verifies archive and
binary SHA-256, and creates a NEW runtime directory. It never updates global
OpenCode. doctor checks binary identity before executing it. Linux x64/ARM64 and
macOS x64/ARM64 assets are pinned; consult docs/opencode-builder.md for the
platforms actually validated for this candidate.

The default provider is scripted, served on an ephemeral loopback port. It drives
real OpenCode read/edit/bash tools through four fixed calls. No model inference
or API account is used. This tests integration plumbing, not reasoning quality.

For a local model, change provider in adapter_config.json:

```json
{"mode":"local","base_url":"http://127.0.0.1:11434/v1","model":"YOUR_INSTALLED_MODEL"}
```

Then run the same demo with --timeout 360. The task and independent checks stay
the same. This supports unauthenticated OpenAI-compatible local endpoints; no
saved credentials are copied. A fixed placeholder Authorization value is sent.
You choose and trust the endpoint; network access is not sandboxed.

run accepts a config file and checks capture validity only. doctor/demo accept
this directory. Every config must declare itself, adapter sources, pin, task and
all fixture files in inputs. Paths stay within this owned workspace. Keep
fixtures free of .env files, secrets, symlinks, local plugins and configuration.
The scripted provider and demo oracle are specific to the reference repair.
For other tasks use a local provider and your own independent outcome checks.

Run roots (.hwb-opencode-*) retain raw JSON events, stderr, generated runtime
configuration, fixture manifests and results. Scripted runs also retain the
provider-visible requests. These records can include local paths and task data;
review them before sharing. Existing --output files are never overwritten.

For sealed Workbench evidence, put runtime/ on PATH then use hwb run coding.json
and hwb verify RUN_ID. Also check the inner outcome and step exit status.
