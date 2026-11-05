#!/usr/bin/env python3
"""Owned OpenCode builder adapter. No universal harness envelope is implied."""
from __future__ import annotations

import argparse
import base64
from contextlib import nullcontext
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

from harness_workbench.capture import (
    capture_bytes, contained_path, digest_file, manifest, minimal_environment, run_bounded, redact_bytes,
)
import provider_auth
import task_checks
from normalizer import summarize
from scripted_provider import ScriptedProvider

CONFIG_SCHEMA = 'opencode-hwb-config/v0.1'
RUN_SCHEMA = 'opencode-hwb-run/v0.1'


def control_runtime(base):
    import importlib.util
    path=regular(base, 'control_runtime.py')
    spec=importlib.util.spec_from_file_location('hwb_opencode_control_runtime', path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def regular(root, name):
    path = contained_path(root, name)
    if not path.is_file() or path.is_symlink():
        raise ValueError('not a regular declared file: ' + str(name))
    return path


def load_config(path):
    config = json.loads(path.read_text())
    allowed = {'schema', 'pin', 'fixture', 'task', 'inputs', 'provider'}
    required_fields = allowed.copy()
    allowed.update({'checks', 'controls'})
    if not isinstance(config, dict) or config.get('schema') != CONFIG_SCHEMA or not required_fields <= set(config) or set(config) - allowed:
        raise ValueError('config requires exactly: ' + ', '.join(sorted(allowed)))
    if 'controls' in config:
        from harness_workbench._adapter_controls import validate as validate_controls
        if config['controls'] is None:
            raise ValueError('controls must be an object when present')
        validate_controls(config['controls'], 'opencode')
    root = path.parent
    # Old scripted/local configs predate these helpers. Migrate only that
    # complete legacy shape in memory; never rewrite the owner's config.
    helpers = {'provider_auth.py', 'task_checks.py'}
    if (isinstance(config['inputs'], list) and all(isinstance(n, str) for n in config['inputs'])
            and not helpers.intersection(config['inputs']) and 'checks' not in config
            and isinstance(config['provider'], dict)
            and config['provider'].get('mode') in ('scripted', 'local')):
        config['inputs'] = [*config['inputs'], *sorted(helpers)]
        config['input_migration'] = sorted(helpers)
    inputs = config['inputs']
    if not isinstance(inputs, list) or not inputs or not all(isinstance(n, str) for n in inputs):
        raise ValueError('inputs must be a nonempty list of relative files')
    if len(set(inputs)) != len(inputs):
        raise ValueError('duplicate declared inputs')
    for name in inputs:
        regular(root, name)
    fixture = contained_path(root, config['fixture'])
    if not fixture.is_dir() or fixture.is_symlink():
        raise ValueError('fixture must be an owned directory')
    required = {path.name, config['pin'], config['task'], 'adapter.py', 'normalizer.py', 'scripted_provider.py', 'provider_auth.py', 'task_checks.py'}
    for item in fixture.rglob('*'):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError('fixture contains a link or special file')
        if item.is_file():
            required.add(item.relative_to(root).as_posix())
    missing = required - set(inputs)
    if missing:
        raise ValueError('inputs must declare itself and all adapter/task/fixture files: ' + ', '.join(sorted(missing)))
    provider_auth.validate(config['provider'], {'anthropic', 'openai', 'google', 'openrouter'})
    if 'checks' in config:
        task_checks.validate(config['checks'])
        if 'task_checks.py' not in inputs:
            raise ValueError('inputs must declare task_checks.py for independent checks')
    if config.get('controls', {}).get('values'):
        from harness_workbench._opencode_controls import validate_config
        validate_config(config, root)
        if 'control_runtime.py' not in config['inputs']:
            raise ValueError('controls require declared control_runtime.py input')
        regular(root, 'control_runtime.py')
    return config


def environment(root):
    overrides = {
        **{'XDG_' + key.upper() + '_HOME': str(root / key) for key in ('config', 'data', 'cache', 'state')},
        'TMPDIR': str(root / 'tmp'),
        'OPENCODE_TEST_HOME': str(root / 'home'),
        'OPENCODE_DISABLE_PROJECT_CONFIG': 'true',
        'OPENCODE_DISABLE_MODELS_FETCH': 'true',
        'OPENCODE_DISABLE_AUTOUPDATE': 'true',
        'OPENCODE_DISABLE_DEFAULT_PLUGINS': 'true',
        'OPENCODE_PURE': 'true',
        'OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER': 'true',
        # Upstream attempts a detached plugin dependency install even in pure
        # mode. It is unused here; prohibit registry fetches and install scripts.
        'npm_config_offline': 'true',
        'npm_config_ignore_scripts': 'true',
        'npm_config_cache': str(root / 'npm-cache'),
        'npm_config_userconfig': str(root / 'empty.npmrc'),
        'npm_config_globalconfig': str(root / 'empty-global.npmrc'),
    }
    for name in ('config', 'data', 'cache', 'state', 'tmp'):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / 'empty.npmrc').touch()
    (root / 'empty-global.npmrc').touch()
    return minimal_environment(root, overrides, passthrough=('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT'))


def process_record(result, redactions=()):
    record = asdict(result)
    record['stdout'] = capture_bytes(result.stdout, source_bytes=result.stdout_source_bytes, redactions=redactions)
    record['stderr'] = capture_bytes(result.stderr, source_bytes=result.stderr_source_bytes, redactions=redactions)
    return record


def clean_exit(result):
    descendants_ok = not hasattr(result, 'descendant_cleanup') or (
        isinstance(result.descendant_cleanup, dict) and result.descendant_cleanup.get('passed') is True)
    return (result.returncode == 0 and result.termination_reason is None
            and not result.group_alive_after_cleanup and descendants_ok)


def doctor(config_path, launcher, root):
    config = load_config(config_path)
    pin = json.loads(regular(config_path.parent, config['pin']).read_text())
    if pin.get('schema') != 'opencode-hwb-pin/v0.1':
        raise ValueError('unsupported runtime pin schema')
    key = platform.system().lower() + '-' + platform.machine().lower()
    if key not in pin['platforms']:
        raise ValueError('runtime platform not pinned: ' + key)
    # Managed host policy cannot be isolated with HOME/XDG. Refuse such
    # environments instead of bypassing policy or merging unrecorded settings.
    managed = Path('/Library/Application Support/opencode') if platform.system() == 'Darwin' else Path('/etc/opencode')
    if any((managed / name).exists() for name in ('opencode.json', 'opencode.jsonc')):
        raise ValueError('managed host OpenCode config is outside this isolated integration scope')
    if platform.system() == 'Darwin':
        import getpass
        prefs = Path('/Library/Managed Preferences')
        if any(p.exists() for p in (prefs / 'ai.opencode.managed.plist',
                prefs / getpass.getuser() / 'ai.opencode.managed.plist')):
            raise ValueError('managed OpenCode preferences are outside this isolated integration scope')
    resolved = shutil.which(launcher)
    if not resolved:
        raise ValueError('OpenCode launcher not found: ' + launcher)
    binary = Path(resolved).resolve(strict=True)
    observed = digest_file(binary)
    if observed != pin['platforms'][key]['binary_sha256']:
        raise ValueError('OpenCode binary identity mismatch; use the pinned official runtime')
    result = run_bounded([str(binary), '--version'], cwd=root, env=environment(root),
        timeout=10, stdout_limit=65536, stderr_limit=65536, termination_grace=1)
    if not clean_exit(result) or result.stdout.decode().strip() != pin['version']:
        raise ValueError('OpenCode version probe failed')
    return config, binary, {'version': pin['version'], 'binary_sha256': observed,
        'platform': key, 'source_commit': pin['source_commit'], 'probe': process_record(result)}


def runtime_config(provider, base_url):
    model = provider.get('model', 'repair')
    return {
        '$schema': 'https://opencode.ai/config.json', 'autoupdate': False,
        'share': 'disabled', 'snapshot': False,
        'enabled_providers': ['hwb'], 'model': 'hwb/' + model, 'small_model': 'hwb/' + model,
        'provider': {'hwb': {'npm': ({'anthropic':'@ai-sdk/anthropic','openai':'@ai-sdk/openai','google':'@ai-sdk/google',
                'openrouter':'@openrouter/ai-sdk-provider'}[provider['name']] if provider.get('mode')=='native' else '@ai-sdk/openai-compatible'), 'name': 'Workbench explicit endpoint',
            'options': {'baseURL': base_url, 'apiKey': '{env:HWB_SELECTED_PROVIDER_KEY}' if provider.get('api_key_env') else 'hwb-local-no-credential'},
            'models': {model: {'name': model, 'tool_call': True,
                'limit': {'context': provider.get('context_window',32768), 'output': provider.get('max_tokens',4096)}}}}},
        'permission': {'*': 'deny', 'read': 'allow', 'bash': 'allow', 'edit': 'allow'},
        'lsp': False, 'formatter': False, 'plugin': [], 'mcp': {},
    }


def evaluate_demo(record, before_test, after_test, scripted, redactions=()):
    errors = list(record['verdict']['errors'])
    before = {p['path']: p for p in record['workspace']['before']}
    after = {p['path']: p for p in record['workspace']['after']}
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    if changed != ['slugger.py']:
        errors.append('expected only slugger.py to change')
    if before_test.returncode != 1 or before_test.termination_reason is not None or before_test.group_alive_after_cleanup:
        errors.append('independent baseline did not reproduce test failure')
    if not clean_exit(after_test):
        errors.append('independent final tests failed')
    tools = record['summary']['tools']
    bash = [t for t in tools if t['tool'] == 'bash'
            and 'unittest' in t.get('input', {}).get('command', '')]
    exits = [t['metadata'].get('exit') if isinstance(t['metadata'], dict) else None for t in bash]
    if not exits or exits[0] != 1 or exits[-1] != 0:
        errors.append('native bash did not reproduce failure then pass')
    if scripted and not any(t['tool'] == 'read' and t['status'] == 'completed' for t in tools):
        errors.append('missing successful native read')
    if scripted and not any(t['tool'] == 'edit' and t['status'] == 'completed' for t in tools):
        errors.append('missing successful native edit')
    if scripted and [t['call_id'] for t in tools] != [f'hwb-repair-{n}' for n in range(4)]:
        errors.append('scripted tool sequence changed')
    return {'passed': not errors, 'errors': errors, 'changed_paths': changed,
        'native_bash_exits': exits, 'independent_before': process_record(before_test, redactions),
        'independent_after': process_record(after_test, redactions),
        'scope': 'coding repair only; no claim about arbitrary workload correctness'}


def capture(config_path, launcher, timeout, parent, demo=False, check_only=False):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    root = Path(tempfile.mkdtemp(prefix='.hwb-opencode-', dir=parent)).resolve()
    config, binary, identity = doctor(config_path, launcher, root)
    record = {'schema': RUN_SCHEMA, 'runtime': identity, 'workspace': {'retained_root': str(root)}}
    if 'controls' in config:
        from harness_workbench._adapter_controls import evidence as control_evidence
        record['controls'] = control_evidence(config['controls'])
    if check_only:
        record['verdict'] = {'passed': True, 'errors': [], 'scope': 'runtime identity and config validation only'}
        return record
    base = config_path.parent
    record['input_migration'] = config.get('input_migration', [])
    record['inputs'] = [{'path': n, 'sha256': digest_file(base / n)} for n in sorted(config['inputs'])]
    fixture = root / 'fixture'
    shutil.copytree(base / config['fixture'], fixture)
    for item in fixture.rglob('*'):
        item.chmod(0o755 if item.is_dir() else 0o644)
    record['workspace']['before'] = manifest(fixture)
    env = environment(root)
    test_env = minimal_environment(root / 'independent-home', {'PYTHONDONTWRITEBYTECODE': '1'})
    provider_env, provider_secrets = provider_auth.resolve(config['provider'])
    if config['provider']['mode'] == 'scripted':
        (root / 'original.py').write_bytes((fixture / 'slugger.py').read_bytes())
    test_argv = [sys.executable, '-B', '-m', 'unittest', '-v']
    before_test = run_bounded(test_argv, cwd=fixture, env=test_env, timeout=20, termination_grace=1) if demo else None
    scripted = config['provider']['mode'] == 'scripted'
    context = ScriptedProvider(fixture) if scripted else nullcontext(None)
    with context as provider:
        endpoint = provider.base_url if provider else config['provider']['base_url']
        runtime = runtime_config(config['provider'], endpoint)
        if config.get('controls', {}).get('values'):
            render = control_runtime(base).render
            record['controls'] = render(config['controls'], runtime, root)
            if 'tool_counter' in config['controls']['values']:
                # Explicit owned plugin profile; fresh homes/project discovery/default
                # plugins and offline package restrictions remain in force.
                env.update({'OPENCODE_PURE':'false', 'OPENCODE_DISABLE_CLAUDE_CODE':'true',
                    'OPENCODE_DISABLE_EXTERNAL_SKILLS':'true', 'OPENCODE_DISABLE_LSP_DOWNLOAD':'true'})
        (root / 'runtime.json').write_text(json.dumps(runtime, indent=2) + '\n')
        env.update(provider_env)
        env['OPENCODE_CONFIG'] = str(root / 'runtime.json')
        model = 'hwb/' + config['provider'].get('model', 'repair')
        argv = [str(binary), 'run', '--format', 'json', '--title', 'Workbench repair', '--model', model, regular(base, config['task']).read_text()]
        (root / 'launch.json').write_bytes(redact_bytes((json.dumps({'argv': argv, 'environment': env}, indent=2)+'\n').encode(), provider_secrets)[0])
        result = run_bounded(argv, cwd=fixture, env=env, timeout=timeout,
            stdout_limit=4_194_304, stderr_limit=524_288, termination_grace=2,
            track_descendants=True)
    record['process'] = process_record(result, provider_secrets)
    stored_stdout = base64.b64decode(record['process']['stdout']['base64'])
    stored_stderr = base64.b64decode(record['process']['stderr']['base64'])
    record['summary'] = summarize(stored_stdout)
    (root / 'stdout.jsonl').write_bytes(stored_stdout)
    (root / 'stderr.txt').write_bytes(stored_stderr)
    errors = list(record['summary']['errors'])
    if not clean_exit(result):
        errors.append('process failed, hit a capture bound, or cleanup could not be verified')
    if provider:
        record['scripted_provider'] = {'requests': provider.requests, 'errors': provider.errors}
        (root / 'provider.json').write_text(json.dumps(record['scripted_provider']))
        errors.extend(provider.errors)
    record['workspace']['after'] = manifest(fixture)
    record['inputs_after'] = [{'path': n, 'sha256': digest_file(base / n)} for n in sorted(config['inputs'])]
    if record['inputs_after'] != record['inputs']:
        errors.append('declared source inputs changed during capture')
    record['network'] = {'mode': 'scripted-loopback' if scripted else 'explicit-local-endpoint',
        'base_url': endpoint, 'model': config['provider'].get('model', 'repair'),
        'containment': False, 'ambient_credentials_passed': False,
        'provider': config['provider'], 'selected_credential_passed': bool(provider_secrets)}
    record['verdict'] = {'passed': not errors, 'errors': errors, 'scope': 'capture validity; task correctness unchecked'}
    if demo:
        after_test = run_bounded(test_argv, cwd=fixture, env=test_env, timeout=20, termination_grace=1)
        record['outcome'] = evaluate_demo(record, before_test, after_test, scripted, provider_secrets)
        record['workspace']['after_independent_tests'] = manifest(fixture)
        if record['workspace']['after_independent_tests'] != record['workspace']['after']:
            record['outcome']['errors'].append('independent tests changed fixture files')
            record['outcome']['passed'] = False
    if 'checks' in config:
        record['task_checks'] = task_checks.run(config['checks'], fixture,
            minimal_environment(root / 'checks-home', {'PYTHONDONTWRITEBYTECODE':'1'}), provider_secrets)
        record['workspace']['after_checks'] = manifest(fixture)
    # Tests/checks are part of the run and may accidentally touch source inputs.
    record['inputs_after'] = []
    for n in sorted(config['inputs']):
        try:
            digest = digest_file(regular(base, n))
        except (OSError, ValueError):
            digest = None
        record['inputs_after'].append({'path': n, 'sha256': digest})
    if record['inputs_after'] != record['inputs']:
        error = 'declared source inputs changed during capture'
        if error not in record['verdict']['errors']:
            record['verdict']['errors'].append(error)
        record['verdict']['passed'] = False
        if 'outcome' in record:
            if error not in record['outcome']['errors']:
                record['outcome']['errors'].append(error)
            record['outcome']['passed'] = False
    if config.get('controls', {}).get('values'):
        measure = control_runtime(base).measure
        record['controls'] = measure(record['controls'], record, root)
        record['experiment'] = record['controls']['effect']
        if record['controls'].get('errors'):
            record['verdict']['errors'].extend(record['controls']['errors'])
            record['verdict']['passed'] = False
    (root / 'result.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    return record


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path)
    parser.add_argument('--opencode', default='opencode')
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--workspace-parent', type=Path)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args(argv)
    try:
        path = args.config.resolve(strict=True)
        parent = args.workspace_parent.resolve(strict=True) if args.workspace_parent else path.parent
        if not parent.is_dir():
            raise ValueError('workspace-parent must be an existing directory')
        result = capture(path, args.opencode, args.timeout, parent, args.demo, args.check_only)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'schema': RUN_SCHEMA, 'error': str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0 if result.get('outcome', result['verdict'])['passed'] and result.get('task_checks', {'passed': True})['passed'] and result.get('experiment', {'passed': True})['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
