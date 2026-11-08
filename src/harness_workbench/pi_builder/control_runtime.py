"""Owned rendering and independent effect measurement for the first Pi hook."""
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from harness_workbench.capture import contained_path, digest_file

ARGUMENTS = ['--mode','json','--no-session','--no-extensions','--no-skills',
             '--no-prompt-templates','--no-context-files','--no-approve',
             '--offline','--tools','read,edit,bash']


def selected(config):
    return config.get('controls', {}).get('values', {}).get('tool_call_extension')


def regular(base, name):
    path = base
    for part in name.split('/'):
        path = path / part
        if path.is_symlink():
            raise ValueError('control inputs may not traverse links')
    if not path.is_file():
        raise ValueError('missing regular control input: ' + name)
    return path


def validate_profile(config, config_path):
    control = selected(config)
    if control is None:
        return
    provider = config.get('provider', {})
    url = urlsplit(provider.get('base_url', ''))
    if (provider.get('mode') != 'compatible' or 'api_key_env' in provider
            or provider.get('api', 'openai-completions') != 'openai-completions'
            or url.scheme != 'http' or url.hostname not in ('localhost','127.0.0.1','::1')
            or url.username or url.password or url.query or url.fragment):
        raise ValueError('tool_call_extension requires an unauthenticated loopback Chat Completions provider')
    if config['pi_arguments'] != ARGUMENTS or config['extensions']:
        raise ValueError('tool_call_extension owns the native invocation; use configure defaults and no legacy extensions')
    required = {'control_runtime.py','tool_call_control.mjs',*control['files']}
    if not required <= set(config['inputs']):
        raise ValueError('undeclared control inputs: ' + ', '.join(sorted(required - set(config['inputs']))))
    base = config_path.parent
    for name in required:
        regular(base, name)
    for name, expected in control['files'].items():
        if digest_file(regular(base, name)) != expected:
            raise ValueError('control input SHA256 mismatch: ' + name)
    # This revision measures existing file edits, not directory policy.
    target = contained_path(base / config['fixture'], control['expect']['path'])
    if not target.is_file() or target.is_symlink():
        raise ValueError('control expect.path must select an existing regular fixture file')


def render(config, base, root):
    control = selected(config)
    source = root / 'control-src'; source.mkdir()
    for name in control['files']:
        target = source / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(regular(base, name), target)
    wrapper = root / 'tool_call_control.mjs'
    shutil.copyfile(regular(base, 'tool_call_control.mjs'), wrapper)
    rendered = {'schema':'pi-local-tool-call/v1', 'root':str(source),
                'entry':str(source / control['entry']), 'files':control['files'],
                'settings':control['settings'], 'timeout_ms':control['timeout_ms']}
    path = root / 'control-config.json';path.write_text(json.dumps(rendered)+'\n')
    bound = {p.relative_to(root).as_posix():digest_file(p) for p in [wrapper,path,*source.rglob('*')] if p.is_file()}
    return wrapper, path, bound


def measure(control, trace, summary, before, after, final, workspace):
    """Native attempted call + hook decision + independently manifested file effect."""
    observations = trace.get('jsonl') or []
    errors = list(trace.get('errors', []))
    loaded = [e for e in observations if e.get('type') == 'loaded']
    failures = [e for e in observations if e.get('type') == 'error']
    if len(loaded) != 1 or failures:
        errors.append('control failed loading/execution or lacks one load observation')
    executions = {e['tool_call_id']:e for e in (summary or {}).get('projection', {}).get('tool_executions', [])}
    attempted = []
    for item in observations:
        if item.get('type') != 'decision' or item.get('tool') != control['expect']['tool']:
            continue
        path = item.get('path')
        try:
            resolved = (workspace / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        except (TypeError, ValueError):
            errors.append('malformed observed tool path');continue
        if resolved != (workspace / control['expect']['path']).resolve():
            continue
        native = executions.get(item.get('call_id'))
        native_path = native.get('target_path') if native else None
        native_target = (workspace / native_path).resolve() if isinstance(native_path, str) else None
        if native is None or native.get('tool_name') != item['tool'] or native_target != resolved:
            errors.append('hook decision has no matching native tool execution');continue
        if item.get('block') and native.get('is_error') is not True:
            errors.append('blocked hook has no native error result')
        attempted.append({**item, 'native_is_error':native.get('is_error')})
    maps = [{e['path']:e['sha256'] for e in values} for values in (before,after,final)]
    target = control['expect']['path']
    changed = maps[0].get(target) != maps[1].get(target)
    stable = maps[1].get(target) == maps[2].get(target)
    denied = control['expect']['effect'] == 'unchanged'
    decisions_match = bool(attempted) and all(item.get('block') is denied and item.get('native_is_error') is denied for item in attempted)
    passed = bool(attempted) and decisions_match and not errors and stable and changed == (not denied)
    return {'schema':'hwb-control-evidence/v1', 'activation':{'state':'observed' if len(loaded)==1 and not failures else 'failed', 'observations':observations},
            'effect':{'state':'measured' if attempted and not errors else 'not_established',
                      'passed':passed,'expected':control['expect'], 'changed':changed,
                      'checks_preserved_target':stable,'decisions_match':decisions_match,'attempts':len(attempted), 'errors':errors}}
