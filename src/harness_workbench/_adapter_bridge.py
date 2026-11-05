"""Local-only custom-task bridge into an owned, sealed Workbench attempt.

The worker trusts declared adapter/check code. This is not a sandbox. Runtime
homes/caches never become attachments: only declared inputs, bounded process
capture, inner JSON, and the inspected retained task workspace are selected.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile

from harness_workbench._adapter_controls import capabilities, evidence, validate
from harness_workbench.capture import capture_bytes, minimal_environment, run_bounded, manifest as workspace_manifest

SCHEMA = 'hwb-adapter-bridge/v1'
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular(root, name):
    if (not isinstance(name, str) or not name or '\\' in name or
            PurePosixPath(name).is_absolute() or
            any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('input must be a canonical relative file path')
    path = root
    for part in name.split('/'):
        path = path / part
        if path.is_symlink():
            raise ValueError('input path contains a symbolic link: ' + name)
    if not path.is_file():
        raise ValueError('input is not a regular file: ' + name)
    return path


def read_json(path):
    def constant(value):
        raise ValueError('non-finite JSON value: ' + value)
    return json.loads(path.read_text(), parse_constant=constant)


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def positive(value):
    if isinstance(value, bool):
        raise ValueError('timeout must be numeric, not boolean')
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > 86400:
        raise ValueError('timeout must be finite, positive and at most 86400 seconds')
    return number


def local_request(config, harness):
    if not isinstance(config, dict):
        raise ValueError('adapter config must be an object')
    expected = {'pi': 'pi-hwb-adapter-config/v0.1',
                'opencode': 'opencode-hwb-config/v0.1',
                'hermes': 'hermes-hwb-config/v0.1'}[harness]
    if config.get('schema') != expected:
        raise ValueError('adapter config schema does not match harness')
    if 'controls' in config:
        if config['controls'] is None:
            raise ValueError('controls must be an object when present')
        validate(config['controls'], harness)
    provider = config.get('provider')
    if harness == 'pi' and provider is None:
        # Existing explicit scripted extensions remain trusted code. They are
        # not an authentication import or a promise of network containment.
        args = config.get('pi_arguments', [])
        if any(str(a).split('=')[0] in ('--api-key', '--session', '--resume', '--continue') for a in args):
            raise ValueError('bridge excludes authentication/session argv')
    elif not isinstance(provider, dict) or provider.get('api_key_env') or provider.get('mode') not in ('local', 'scripted', 'compatible'):
        raise ValueError('bridge supports local/scripted providers without credential references only')
    elif provider.get('mode') != 'scripted':
        from urllib.parse import urlsplit
        url = urlsplit(provider.get('base_url', ''))
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
                or url.username or url.password or url.query or url.fragment):
            raise ValueError('bridge endpoint must be unauthenticated HTTP loopback')
    checks = config.get('checks', [])
    if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
        raise ValueError('checks must be a list of objects')
    check_budget = sum(positive(c.get('timeout', 60)) for c in checks)
    return check_budget


def generate(config_path, harness, output, launcher, timeout, uv='uv', feature_refs=None):
    if config_path.is_symlink():
        raise ValueError("config must not be a symbolic link")
    config_path = config_path.resolve(strict=True)
    root = config_path.parent
    config = read_json(regular(root, config_path.name))
    check_budget = local_request(config, harness)
    if output.absolute().parent.resolve(strict=True) != root:
        raise ValueError('generated spec must be beside the adapter config')
    timeout = positive(timeout)
    positive(timeout + check_budget + 120)
    if feature_refs is not None and not isinstance(feature_refs, list):
        raise ValueError('features must be a JSON list')
    inputs = config.get('inputs')
    if (not isinstance(inputs, list) or not inputs or
            any(not isinstance(n, str) for n in inputs) or len(set(inputs)) != len(inputs)):
        raise ValueError('inputs must be a nonempty unique list')
    required = {config_path.name, config.get('pin'), config.get('task'), 'adapter.py'}
    fixture_name = config.get('fixture')
    if not isinstance(fixture_name, str):
        raise ValueError('fixture must be a relative directory')
    # Validate each ancestor even for an empty fixture.
    fixture = root / fixture_name
    if not fixture.is_dir() or any(p.is_symlink() for p in (fixture, *fixture.parents) if p != root.parent):
        raise ValueError('fixture must be an owned regular directory')
    if fixture.resolve().is_relative_to(root.resolve()) is False:
        raise ValueError('fixture escapes config directory')
    for path in tree_files(fixture):
        required.add(path.relative_to(root).as_posix())
    if not required <= set(inputs):
        raise ValueError('inputs omit config/adapter/pin/task/fixture files')
    digests = {name: sha(regular(root, name)) for name in sorted(inputs)}
    if len(inputs) > MAX_FILES or sum(regular(root, n).stat().st_size for n in inputs) > MAX_BYTES // 2:
        raise ValueError('declared inputs exceed bridge retention budget')
    executable = str(Path(launcher).resolve(strict=True)) if launcher else None
    if harness in ('pi', 'opencode') and not executable:
        raise ValueError('an explicit pinned launcher is required')
    if harness == 'hermes' and not executable:
        raise ValueError('an explicit owned Hermes source runtime is required')
    worker = output.with_suffix('.worker.py')
    request_path = output.with_suffix('.bridge.json')
    for path in (output, worker, request_path):
        if path.exists() or path.is_symlink():
            raise ValueError('destination already exists: ' + str(path))
    modes = {n: regular(root, n).stat().st_mode & 0o777 for n in inputs}
    request = {'input_modes': modes, 'schema': SCHEMA, 'harness': harness, 'config': config_path.name,
               'inputs': digests, 'launcher': executable, 'uv': uv,
               'native_timeout': timeout, 'worker_timeout': timeout + check_budget + 90,
               'retention': {'max_bytes': MAX_BYTES, 'max_files': MAX_FILES}}
    # Exclusive creation preserves failed/previous attempts instead of replacing.
    with worker.open('xb') as stream:
        stream.write(Path(__file__).read_bytes())
    write_new(request_path, request)
    spec = {'schema': 'hwbspec/v0.1', 'run_class': 'discovery',
            'features_root': 'harness_workbench:builtin',
            'features': feature_refs or [],
            'step_timeout_ms': math.ceil((request['worker_timeout'] + 30) * 1000),
            'steps': [{'id': harness + '-custom-task', 'attempt_context': 'hwb-attempt/v1',
                       'argv': [sys.executable, '-B', worker.name, 'execute', request_path.name],
                       'inputs': sorted([*inputs, worker.name, request_path.name])}]}
    write_new(output, spec)
    return spec


def write_bounded(path, value, budget):
    data = (json.dumps(value, sort_keys=True, allow_nan=False) + '\n').encode()
    budget['files'] += 1
    budget['bytes'] += len(data)
    if budget['files'] > MAX_FILES or budget['bytes'] > MAX_BYTES - 4 * 1024 * 1024:
        raise ValueError('retained evidence exceeds bridge budget')
    with path.open('xb') as stream:
        stream.write(data)


def tree_files(source):
    """Inspect at most MAX_FILES entries without following links/special files."""
    import stat
    pending = [source]
    leaves = []
    visited = 0
    while pending:
        path = pending.pop()
        visited += 1
        if visited > MAX_FILES:
            raise ValueError('retained tree exceeds bridge entry budget')
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError('unsafe retained symbolic link')
        if stat.S_ISDIR(mode):
            with os.scandir(path) as entries:
                for entry in entries:
                    if visited + len(pending) + len(leaves) > MAX_FILES:
                        raise ValueError('retained tree exceeds bridge entry budget')
                    pending.append(Path(entry.path))
        elif stat.S_ISREG(mode):
            leaves.append(path)
        else:
            raise ValueError('unsafe retained special file')
    return sorted(leaves)


def retain(source, dest, budget):
    """Bounded regular-file copy; reject links/special files, never follow them."""
    if source.is_dir() and not source.is_symlink():
        dest.mkdir(parents=True, exist_ok=True)
    for path in tree_files(source):
        budget['files'] += 1
        budget['bytes'] += path.stat().st_size
        if budget['files'] > MAX_FILES or budget['bytes'] > MAX_BYTES - 4 * 1024 * 1024:
            raise ValueError('retained evidence exceeds bridge budget')
        target = dest / path.relative_to(source) if source.is_dir() else dest
        target.parent.mkdir(parents=True, exist_ok=True)
        # Trusted code; not protection against a concurrently malicious writer.
        shutil.copy2(path, target)


def manifest(root):
    return [{'path': p.relative_to(root).as_posix(), 'sha256': sha(p),
             'bytes': p.stat().st_size} for p in sorted(root.rglob('*')) if p.is_file()]


def execute(request_path):
    context = os.environ.get('HWB_ATTEMPT_DIR')
    if not context:
        raise ValueError('execute requires the Workbench attempt context')
    attempt = Path(context).resolve(strict=True)
    bundle = attempt / 'adapter-evidence'
    bundle.mkdir(mode=0o700)  # never reuse an attempt
    root = request_path.absolute().parent
    request = read_json(request_path)
    summary = {'schema': 'hwb-adapter-evidence/v1', 'capture_valid': False,
               'task_success': None, 'model_reported_success': 'not_normalized',
               'experiment': {'state': 'not_evaluated'},
               'sealed_record_conformance': 'verify_outer_record_separately',
               'errors': [], 'sandbox': False}
    inner = None
    budget = {'files': 0, 'bytes': 0}
    try:
        if (not isinstance(request, dict) or request.get('schema') != SCHEMA
                or set(request) != {'schema','harness','config','inputs','input_modes',
                                    'launcher','uv','native_timeout','worker_timeout','retention'}):
            raise ValueError('invalid bridge request')
        harness = request['harness']
        config = read_json(regular(root, request['config']))
        check_budget = local_request(config, harness)
        timeout = positive(request['native_timeout'])
        if request['worker_timeout'] != timeout + check_budget + 90:
            raise ValueError('bridge timeout/headroom mismatch')
        if request['retention'] != {'max_bytes': MAX_BYTES, 'max_files': MAX_FILES}:
            raise ValueError('unsupported retention policy')
        if not isinstance(request['inputs'], dict) or len(request['inputs']) > MAX_FILES:
            raise ValueError('invalid bridge input manifest')
        if set(request['inputs']) != set(config.get('inputs', [])):
            raise ValueError('bridge manifest must match declared config inputs')
        before = {n: sha(regular(root, n)) for n in request['inputs']}
        if before != request['inputs']:
            raise ValueError('declared input drift since spec generation')
        if {n: regular(root, n).stat().st_mode & 0o777 for n in before} != request['input_modes']:
            raise ValueError('declared input mode drift since spec generation')
        summary['original_inputs_before'] = before
        summary['controls'] = evidence(config.get('controls'))
        summary['implementation'] = {'worker_sha256': sha(Path(__file__)),
                                     'controls_sha256': sha(Path(sys.modules[validate.__module__].__file__)),
                                     'python': sys.version}
        # Runtime private homes are disposable, not indiscriminate attachments.
        with tempfile.TemporaryDirectory(prefix='hwb-adapter-attempt-') as temporary:
            scratch = Path(temporary)
            staged = scratch / 'inputs'; staged.mkdir()
            for name in before:
                source = regular(root, name)
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            if {n: sha(regular(staged, n)) for n in before} != before:
                raise ValueError('staged input copy does not match source')
            fixture_name = config.get('fixture')
            if (not isinstance(fixture_name, str) or not fixture_name
                    or Path(fixture_name).is_absolute() or '\\' in fixture_name
                    or any(p in ('', '.', '..') for p in fixture_name.split('/'))):
                raise ValueError('fixture must be a canonical relative directory')
            # Empty fixtures are legitimate tasks that create new files.
            (staged / fixture_name).mkdir(parents=True, exist_ok=True)
            retained = scratch / 'native'; retained.mkdir()
            argv = [sys.executable, '-B', str(staged / 'adapter.py'),
                    str(staged / request['config']), '--timeout', str(timeout),
                    '--workspace-parent', str(retained)]
            if harness in ('pi', 'opencode'):
                argv.extend(['--' + harness, request['launcher']])
            else:
                argv.extend(['--runtime', request['launcher'], '--uv', request['uv']])
            env = minimal_environment(scratch / 'worker-home', {'PYTHONDONTWRITEBYTECODE': '1'})
            # Source checkouts need this import path; do not propagate a caller's
            # arbitrary PYTHONPATH or credentials into the adapter child.
            import harness_workbench
            env['PYTHONPATH'] = str(Path(harness_workbench.__file__).parent.parent)
            proc = run_bounded(argv, cwd=staged, env=env,
                               timeout=request['worker_timeout'],
                               stdout_limit=16 * 1024 * 1024, stderr_limit=1024 * 1024,
                               termination_grace=3, track_descendants=True)
            process = asdict(proc)
            process['stdout'] = capture_bytes(proc.stdout, source_bytes=proc.stdout_source_bytes)
            process['stderr'] = capture_bytes(proc.stderr, source_bytes=proc.stderr_source_bytes)
            write_bounded(bundle / 'adapter-process.json', process, budget)
            try:
                inner = json.loads(proc.stdout)
                if not isinstance(inner, dict) or 'workspace' not in inner or 'verdict' not in inner:
                    raise ValueError('adapter returned no complete inner result')
                write_bounded(bundle / 'inner-result.json', inner, budget)
                native_root = Path(inner['workspace']['retained_root'])
                if (native_root != native_root.resolve(strict=True)
                        or not native_root.is_relative_to(retained) or native_root.is_symlink()
                        or not native_root.is_dir()):
                    raise ValueError('adapter result references an unowned retained root')
                if any(p.is_symlink() for p in (native_root, *native_root.parents)):
                    raise ValueError('adapter retained root contains a symbolic link')
                fixture = native_root / ('workspace' if harness == 'pi' else 'fixture')
                if not fixture.is_dir() or fixture.is_symlink():
                    raise ValueError('adapter retained fixture missing or unsafe')
                retain(fixture, bundle / 'workspace', budget)
                inspected = workspace_manifest(bundle / 'workspace')
                expected = inner['workspace'].get('after_checks', inner['workspace'].get('after'))
                summary['retained_workspace_manifest'] = inspected
                if inspected != expected:
                    raise ValueError('retained fixture does not match adapter final manifest')
                # Native output/config sidecars only; never runtime homes,
                # accounts, caches, databases or arbitrary native-root files.
                for name in ('pi-stdout.jsonl', 'pi-stderr.bin', 'stdout.jsonl', 'stderr.bin', 'runtime.json', 'control.trace.jsonl', 'control-config.json', 'tool_call_control.mjs', 'control-src', 'control-plugin.mjs', 'control-hook.py', 'gateway-cleanup.json', 'launch.json', 'stderr.txt'):
                    path = native_root / name
                    if path.exists() or path.is_symlink():
                        retain(path, bundle / 'native' / name, budget)
                summary['capture_valid'] = (inner['verdict'].get('passed') is True
                    and proc.termination_reason is None
                    and not proc.group_alive_after_cleanup
                    and isinstance(proc.descendant_cleanup, dict)
                    and proc.descendant_cleanup.get('passed') is True
                    and proc.returncode in (0, 1))
                if harness in ('pi', 'opencode', 'hermes') and inner.get('controls', {}).get('effect', {}).get('state'):
                    summary['controls'] = inner['controls']
                    effect = inner['controls']['effect']
                    if 'passed' in effect:
                        summary['experiment'] = effect
                checks = inner.get('task_checks')
                summary['task_success'] = checks.get('passed') if config.get('checks') and isinstance(checks, dict) else None
                summary['workspace_attachment'] = 'workspace'
                summary['inner_paths'] = 'historical scratch paths; use bundle-relative attachments'
            except (OSError, ValueError, KeyError, TypeError) as error:
                summary['errors'].append(str(error))
                # A timed-out/setup-failed client can leave useful partial
                # fixtures/streams without a complete inner JSON result.
                for index, partial in enumerate(sorted(retained.iterdir())):
                    if not partial.is_dir() or partial.is_symlink():
                        continue
                    for name in ('workspace', 'fixture', 'pi-stdout.jsonl', 'pi-stderr.bin', 'stdout.jsonl', 'stderr.bin'):
                        source = partial / name
                        if source.exists() or source.is_symlink():
                            try:
                                retain(source, bundle / 'partial-native' / str(index) / name, budget)
                            except (OSError, ValueError) as partial_error:
                                summary['errors'].append(str(partial_error))
            # Always retain staged declarations, including setup-failure cases.
            retain(staged, bundle / 'inputs', budget)
            summary['staged_inputs_after'] = {n: sha(regular(staged, n)) for n in before}
            if {n: regular(staged, n).stat().st_mode & 0o777 for n in before} != request['input_modes']:
                summary['errors'].append('staged source input modes changed')
            if summary['staged_inputs_after'] != before:
                summary['errors'].append('staged source inputs changed')
        summary['original_inputs_after'] = {n: sha(regular(root, n)) for n in before}
        if {n: regular(root, n).stat().st_mode & 0o777 for n in before} != request['input_modes']:
            summary['errors'].append('original source input modes changed')
        if summary['original_inputs_after'] != before:
            summary['errors'].append('original source inputs changed')
    except (OSError, ValueError, KeyError, TypeError) as error:
        summary['errors'].append(str(error))
    if summary['errors']:
        summary['capture_valid'] = False
    summary['attachments'] = manifest(bundle)
    encoded = (json.dumps(summary, sort_keys=True, allow_nan=False) + '\n').encode()
    if len(encoded) > 4 * 1024 * 1024:
        summary = {'schema': 'hwb-adapter-evidence/v1', 'capture_valid': False,
                   'task_success': None, 'errors': ['bridge summary exceeds reserved budget']}
        encoded = (json.dumps(summary) + '\n').encode()
    with (bundle / 'bridge-result.json').open('xb') as stream:
        stream.write(encoded)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary['capture_valid'] and summary['task_success'] is True and summary['experiment'].get('passed', True) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog='hwb-adapter')
    sub = parser.add_subparsers(dest='command', required=True)
    caps = sub.add_parser('capabilities'); caps.add_argument('harness', choices=('pi', 'opencode', 'hermes'))
    gen = sub.add_parser('spec', help='generate a local custom-task outer spec beside its config')
    gen.add_argument('harness', choices=('pi', 'opencode', 'hermes'))
    gen.add_argument('config', type=Path); gen.add_argument('--output', type=Path, required=True)
    gen.add_argument('--launcher', required=True, help='pinned Pi/OpenCode launcher, or owned Hermes source directory')
    gen.add_argument('--timeout', type=float, default=120); gen.add_argument('--uv', default='uv')
    gen.add_argument('--features', type=json.loads, default=[], help='outer Workbench feature references as a JSON list')
    worker = sub.add_parser('execute'); worker.add_argument('request', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'capabilities':
            print(json.dumps(capabilities(args.harness), indent=2)); return 0
        if args.command == 'spec':
            generate(args.config, args.harness, args.output, args.launcher, args.timeout, args.uv, args.features)
            print('Created ' + str(args.output)); return 0
        return execute(args.request)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'schema': SCHEMA, 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
