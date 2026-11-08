"""Versioned native-control contract. Discovery is not implementation.

Pi, OpenCode and Hermes expose narrow declared control profiles; unsupported
controls fail closed.
Adapters deliberately reject proposed values until a renderer AND effect tests
exist for the pinned harness. Absence preserves the legacy adapter behavior.
"""
from __future__ import annotations

SCHEMA = 'hwb-native-controls/v1'
VERSIONS = {'pi': '1.4.0', 'opencode': '1.19.5', 'hermes': '0.22.5'}
FAMILIES = ('tool_selection', 'tool_denial', 'hooks_extensions', 'instructions',
            'sessions_resume', 'persistent_memory', 'reasoning_and_limits',
            'native_retry', 'compaction')


def capabilities(harness):
    if harness not in VERSIONS:
        raise ValueError('unknown adapter: ' + str(harness))
    return {'schema': 'hwb-native-capabilities/v1', 'harness': harness,
            'native_version': VERSIONS[harness], 'controls_schema': SCHEMA,
            'implemented_values': ({'tool_call_extension': 'pi-local-tool-call/v1'} if harness == 'pi' else {'edit_permission': 'opencode-local-edit/v1', 'tool_counter': 'opencode-owned-counter/v1'} if harness == 'opencode' else {'tool_selection':'hermes-local-toolsets/v1','patch_hook':'hermes-owned-patch-hook/v1'}),
            'families': {name: ('tool_call_hook_and_edit_effect_oracle' if harness == 'pi' and name in ('tool_denial','hooks_extensions') else 'owned_permission_and_counter_profile' if harness == 'opencode' and name in ('tool_denial','hooks_extensions') else 'owned_toolset_and_patch_hook_profile' if harness == 'hermes' and name in ('tool_selection','tool_denial','hooks_extensions') else 'not_exposed_by_controls_v1') for name in FAMILIES},
            'legacy_defaults': 'unchanged; see adapter config and native invocation',
            'sandbox': False}


def validate(value, harness):
    """Return a fresh normalized request, or fail before native execution."""
    capabilities(harness)
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'schema', 'harness', 'values'}:
        raise ValueError('controls requires exactly schema, harness, values')
    if value['schema'] != SCHEMA or value['harness'] != harness:
        raise ValueError('controls schema/harness does not match adapter')
    if not isinstance(value['values'], dict):
        raise ValueError('controls.values must be an object')
    values = value['values']
    allowed = {'tool_call_extension'} if harness == 'pi' else {'edit_permission', 'tool_counter'} if harness == 'opencode' else {'tool_selection','patch_hook'}
    if set(values) - allowed:
        raise ValueError('unsupported native control(s): ' + ', '.join(sorted(set(values) - allowed)))
    if 'tool_call_extension' in values:
        validate_tool_call(values['tool_call_extension'])
    if harness == 'opencode' and values:
        from harness_workbench._opencode_controls import validate
        validate(values)
    if harness == 'hermes' and values:
        from harness_workbench._hermes_controls import validate
        validate(values)
    import copy
    return {'schema': SCHEMA, 'harness': harness, 'values': copy.deepcopy(values)}


def evidence(request):
    """Never turn requested configuration into an activation/effect claim."""
    return {'schema': 'hwb-control-evidence/v1', 'requested': request,
            'rendered': {'state': 'legacy_adapter_defaults', 'control_overrides': {}},
            'activation': {'state': 'not_measured', 'observations': []},
            'effect': {'state': 'not_evaluated', 'observations': []}}


def relative_file(name):
    if (not isinstance(name, str) or not name or name.startswith('/') or '\\' in name
            or any(p in ('', '.', '..', 'node_modules') for p in name.split('/'))):
        raise ValueError('control paths must be canonical relative files')
    return name


def validate_tool_call(control):
    import re
    import json
    if not isinstance(control, dict) or set(control) != {'entry', 'files', 'settings', 'expect', 'timeout_ms'}:
        raise ValueError('tool_call_extension requires entry, files, settings, expect, timeout_ms')
    entry = relative_file(control['entry'])
    files = control['files']
    if not isinstance(files, dict) or not files or len(files) > 64 or entry not in files:
        raise ValueError('control files must bind entry and at most 64 dependency files')
    if not entry.endswith('.mjs'):
        raise ValueError('control entry must be an ES module (.mjs)')
    for name, digest in files.items():
        relative_file(name)
        if not name.endswith(('.mjs', '.json')) or name.split('/')[-1] == 'package.json':
            raise ValueError('control dependencies support .mjs/.json only, without package profiles')
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('control files require lowercase SHA256 digests')
    if not isinstance(control['settings'], dict) or len(json.dumps(control['settings'], allow_nan=False).encode()) > 16384:
        raise ValueError('control settings must be a JSON object of at most 16KiB')
    expected = control['expect']
    if (not isinstance(expected, dict) or set(expected) != {'tool', 'path', 'effect'}
            or expected['tool'] != 'edit' or expected['effect'] not in ('changed', 'unchanged')):
        raise ValueError('control expect requires edit, relative path and changed/unchanged effect')
    relative_file(expected['path'])
    if type(control['timeout_ms']) is not int or not 1 <= control['timeout_ms'] <= 5000:
        raise ValueError('control timeout_ms must be 1..5000')
