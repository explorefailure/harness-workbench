#!/usr/bin/env python3
"""Owned Hermes builder adapter. No universal harness envelope is implied."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlsplit
from runtime_identity import environment, verify_runtime

from harness_workbench.capture import (
    capture_bytes, contained_path, digest_file, manifest, minimal_environment, run_bounded,
)
from normalizer import summarize
from scripted_provider import ScriptedProvider

CONFIG_SCHEMA = 'hermes-hwb-config/v0.1'
RUN_SCHEMA = 'hermes-hwb-run/v0.1'


def regular(root, name):
    path = contained_path(root, name)
    if not path.is_file() or path.is_symlink():
        raise ValueError('not a regular declared file: ' + str(name))
    return path


def load_config(path):
    config = json.loads(path.read_text())
    allowed = {'schema', 'pin', 'fixture', 'task', 'inputs', 'provider'}
    if not isinstance(config, dict) or config.get('schema') != CONFIG_SCHEMA or set(config) != allowed:
        raise ValueError('config requires exactly: ' + ', '.join(sorted(allowed)))
    root = path.parent
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
    required = {path.name, config['pin'], config['task'], 'adapter.py', 'normalizer.py', 'scripted_provider.py', 'runtime_identity.py', 'gateway_cleanup.py'}
    for item in fixture.rglob('*'):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):
            raise ValueError('fixture contains a link or special file')
        if item.is_file():
            required.add(item.relative_to(root).as_posix())
    missing = required - set(inputs)
    if missing:
        raise ValueError('inputs must declare itself and all adapter/task/fixture files: ' + ', '.join(sorted(missing)))
    provider = config['provider']
    if not isinstance(provider, dict):
        raise ValueError('provider must be an object')
    if provider.get('mode') == 'scripted':
        if set(provider) != {'mode'}:
            raise ValueError('scripted provider accepts only mode')
    elif provider.get('mode') == 'local':
        if set(provider) != {'mode', 'base_url', 'model'}:
            raise ValueError('local provider requires mode, base_url and model')
        url = urlsplit(provider['base_url'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('base_url must be an HTTP(S) endpoint without credentials, query or fragment')
        model = provider['model']
        if not isinstance(model, str) or not model.strip() or any(c.isspace() for c in model):
            raise ValueError('model must be a nonempty model ID without whitespace')
    else:
        raise ValueError('provider mode must be scripted or local')
    return config


def process_record(result):
    record = asdict(result)
    record['stdout'] = capture_bytes(result.stdout, source_bytes=result.stdout_source_bytes)
    record['stderr'] = capture_bytes(result.stderr, source_bytes=result.stderr_source_bytes)
    return record


def clean_exit(result):
    return result.returncode == 0 and result.termination_reason is None and not result.group_alive_after_cleanup


def doctor(config_path, runtime, root, uv):
    config = load_config(config_path)
    pin = json.loads(regular(config_path.parent, config['pin']).read_text())
    if pin.get('schema') != 'hermes-hwb-pin/v0.1':
        raise ValueError('unsupported Hermes pin schema')
    source = Path(runtime).resolve(strict=True) if runtime else config_path.parent / 'runtime/source'
    python, identity = verify_runtime(source, pin, root, uv)
    return config, python, identity


def evaluate_demo(record, before_test, after_test, scripted):
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
    bash = [t for t in tools if t['tool'] == 'terminal'
            and 'unittest' in t.get('input', {}).get('command', '')]
    exits = [t['exit_code'] for t in bash]
    if not exits or exits[0] != 1 or exits[-1] != 0:
        errors.append('native terminal did not reproduce failure then pass')
    if scripted and not any(t['tool'] == 'read_file' and not t['is_error'] for t in tools):
        errors.append('missing successful native read')
    if scripted and not any(t['tool'] == 'patch' and not t['is_error'] for t in tools):
        errors.append('missing successful native edit')
    if scripted and [t['call_id'] for t in tools] != [f'hwb-repair-{n}' for n in range(4)]:
        errors.append('scripted tool sequence changed')
    return {'passed': not errors, 'errors': errors, 'changed_paths': changed,
        'native_terminal_exits': exits, 'independent_before': process_record(before_test),
        'independent_after': process_record(after_test),
        'scope': 'coding repair only; no claim about arbitrary workload correctness'}


def capture(config_path, runtime, timeout, parent, demo=False, check_only=False, uv="uv"):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    root = Path(tempfile.mkdtemp(prefix='.hwb-hermes-', dir=parent)).resolve()
    config, python, identity = doctor(config_path, runtime, root, uv)
    record = {'schema': RUN_SCHEMA, 'runtime': identity, 'workspace': {'retained_root': str(root)}}
    if check_only:
        record['verdict'] = {'passed': True, 'errors': [], 'scope': 'runtime identity and config validation only'}
        return record
    base = config_path.parent
    record['inputs'] = [{'path': n, 'sha256': digest_file(base / n)} for n in sorted(config['inputs'])]
    fixture = root / 'fixture'
    shutil.copytree(base / config['fixture'], fixture)
    for item in fixture.rglob('*'):
        item.chmod(0o755 if item.is_dir() else 0o644)
    record['workspace']['before'] = manifest(fixture)
    env = environment(root)
    if config['provider']['mode'] == 'scripted':
        (root / 'original.py').write_bytes((fixture / 'slugger.py').read_bytes())
    test_argv = [sys.executable, '-B', '-m', 'unittest', '-v']
    before_test = run_bounded(test_argv, cwd=fixture, env=env, timeout=20, termination_grace=1) if demo else None
    scripted = config['provider']['mode'] == 'scripted'
    context = ScriptedProvider(fixture) if scripted else nullcontext(None)
    with context as provider:
        endpoint = provider.base_url if provider else config['provider']['base_url']
        model = config['provider'].get('model', 'repair')
        env['TERMINAL_CWD'] = str(fixture)
        argv = [str(python), str(Path(identity['source_root']) / 'hermes'),
                'chat', '--format', 'stream-json', '--safe-mode', '--provider', 'custom',
                '--base-url', endpoint, '--api-key', 'hwb-local-no-credential', '--model', model,
                '--toolsets', 'file,terminal', '--max-turns', '8', '--yolo',
                '--query', regular(base, config['task']).read_text()]
        (root / 'launch.json').write_text(json.dumps({'argv': argv, 'environment': env}, indent=2) + '\n')
        try:
            result = run_bounded(argv, cwd=fixture, env=env, timeout=timeout,
                stdout_limit=4_194_304, stderr_limit=524_288, termination_grace=2)
        finally:
            stopped = run_bounded([str(python), str(base / 'gateway_cleanup.py'),
                identity['source_root'], env['HERMES_HOME']], cwd=fixture, env=env,
                timeout=20, stdout_limit=65_536, stderr_limit=65_536, termination_grace=1)
            (root / 'gateway-cleanup.stdout').write_bytes(stopped.stdout)
            (root / 'gateway-cleanup.stderr').write_bytes(stopped.stderr)
            try:
                cleanup = json.loads(stopped.stdout)
            except (ValueError, UnicodeError):
                cleanup = {'passed': False, 'error': 'invalid cleanup response'}
            record['gateway_cleanup'] = {'result': cleanup, 'process': process_record(stopped)}
            (root / 'gateway-cleanup.json').write_text(json.dumps(record['gateway_cleanup']))
    record['process'] = process_record(result)
    record['summary'] = summarize(result.stdout)
    (root / 'stdout.jsonl').write_bytes(result.stdout)
    (root / 'stderr.txt').write_bytes(result.stderr)
    errors = list(record['summary']['errors'])
    if not clean_exit(stopped) or not cleanup.get('passed'):
        errors.append('owned Hermes gateway cleanup failed')
    if not clean_exit(result):
        errors.append('process failed, hit a capture bound, or retained a surviving process')
    if provider:
        record['scripted_provider'] = {'requests': provider.requests, 'probes': provider.probes, 'errors': provider.errors}
        (root / 'provider.json').write_text(json.dumps(record['scripted_provider']))
        errors.extend(provider.errors)
    record['workspace']['after'] = manifest(fixture)
    record['inputs_after'] = [{'path': n, 'sha256': digest_file(base / n)} for n in sorted(config['inputs'])]
    if record['inputs_after'] != record['inputs']:
        errors.append('declared source inputs changed during capture')
    record['network'] = {'mode': 'scripted-loopback' if scripted else 'explicit-local-endpoint',
        'base_url': endpoint, 'model': config['provider'].get('model', 'repair'),
        'containment': False, 'ambient_credentials_passed': False}
    record['verdict'] = {'passed': not errors, 'errors': errors, 'scope': 'capture validity; task correctness unchecked'}
    if demo:
        after_test = run_bounded(test_argv, cwd=fixture, env=env, timeout=20, termination_grace=1)
        record['outcome'] = evaluate_demo(record, before_test, after_test, scripted)
        record['workspace']['after_independent_tests'] = manifest(fixture)
        if record['workspace']['after_independent_tests'] != record['workspace']['after']:
            record['outcome']['errors'].append('independent tests changed fixture files')
            record['outcome']['passed'] = False
    (root / 'result.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    return record


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--uv', default='uv')
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--workspace-parent', type=Path)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args(argv)
    try:
        path = args.config.resolve(strict=True)
        parent = args.workspace_parent.resolve(strict=True) if args.workspace_parent else path.parent
        if not parent.is_dir():
            raise ValueError('workspace-parent must be an existing directory')
        result = capture(path, args.runtime, args.timeout, parent, args.demo, args.check_only, args.uv)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'schema': RUN_SCHEMA, 'error': str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0 if result.get('outcome', result['verdict'])['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
