# Owned Hermes builder workspace

Requires this Workbench candidate and optional uv 0.12.21. Installation is explicit:

```sh
python3 install_runtime.py
hwb-hermes doctor .
hwb-hermes demo . --output repair.result.json
```

The installer creates NEW runtime/, clones the exact Hermes 0.22.5 release,
verifies the annotated tag, commit, tracked source bytes and uv.lock, and uses
Hermes' supported editable source install with a frozen dependency graph and
Python 3.14.4. The managed Python and dependencies remain inside runtime/.
It never updates global Hermes or changes your provider accounts.

Use --runtime /absolute/path/to/hermes-source to reuse an exactly matching source
installation. Doctor rejects source drift and checks the realized environment
with uv sync --frozen --check --offline. The version probe disables update checks.
The default source is runtime/source. Keep uv on PATH for doctor/demo/run.

The default provider is scripted. A local server supplies model-shaped responses
while actual Hermes read_file, terminal and patch tools repair the fixture.
No model inference or account is used. An expected Ollama metadata probe gets
an explicit recorded 404 because this fixture is not an Ollama server.

For a local model, edit provider in adapter_config.json:

```json
{"mode":"local","base_url":"http://127.0.0.1:11434/v1","model":"YOUR_INSTALLED_MODEL"}
```

Then run hwb-hermes demo . --timeout 600 --output local.result.json.
The server must have tool support and sufficient context; the adapter does not
reconfigure it. Only unauthenticated OpenAI-compatible endpoints are supported.
A fixed dummy key is used; saved account credentials are not copied.

Demo checks independent failing/passing tests, unchanged test bytes/mode, only
slugger.py changed, native terminal failure then success, a valid native result
and clean bounded process termination. Run checks capture only; custom tasks
need their own independent oracle. The scripted provider is repair-specific.

Every config declares itself, runtime pin, task, adapter/normalizer/provider/
runtime_identity/gateway_cleanup sources and every fixture file in inputs. Declared paths stay
inside the owned workspace and fixture links/special files are refused.

Native Hermes CLI tool-result strings are capped upstream at 5000 characters.
The adapter preserves exactly those native bytes; it does not invent the missing
payload. It keeps reported is_error separate from a decoded terminal exit code.
A missing/undecodable exit cannot pass the reference repair oracle.

Run roots (.hwb-hermes-*) retain raw stdout/stderr, effective launch, manifests,
result and scripted provider requests/probes. Review task data and paths before
sharing. Existing --output files are never overwritten. Initialization and the
installer refuse existing destinations.

For a sealed reference run use hwb run coding.json and hwb verify RUN_ID. Its
wrapper selects runtime/source by default. To reuse another pinned runtime pass
--runtime to run_demo.sh, update coding.json's argv accordingly, then freeze a
NEW owned setup. Inspect inner outcome and step exit as well as outer integrity.

Hermes launches a separate per-run gateway. The adapter records bounded cleanup
of that isolated home, checks PID/start-time ownership before signaling, and
requires cleanup success. It never scans or stops other profiles/services.
