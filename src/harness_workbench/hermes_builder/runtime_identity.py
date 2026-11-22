"""Pinned source/lock verification and bounded optional-runtime probes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from harness_workbench.capture import digest_file, minimal_environment, run_bounded


def probe(argv, cwd, env, timeout=15):
    result = run_bounded(list(map(str, argv)), cwd=cwd, env=env, timeout=timeout,
                         stdout_limit=1_048_576, stderr_limit=262144, termination_grace=1)
    if result.returncode or result.termination_reason or result.group_alive_after_cleanup:
        raise ValueError('runtime probe failed: ' + str(argv[0]) + ': ' + result.stderr.decode(errors='replace')[:1000])
    return result.stdout.decode().strip()


def environment(root):
    root = root.resolve()
    overrides = {
        'HERMES_HOME': str(root / 'hermes-home'), 'HERMES_DISABLE_LAZY_INSTALLS': '1',
        'HERMES_DISABLE_FAST_CHAT_LAUNCH': '1', 'UV_OFFLINE': '1',
        'UV_CACHE_DIR': str(root / 'uv-cache'), 'PYTHONDONTWRITEBYTECODE': '1',
        'TMPDIR': str(root / 'tmp'),
        **{'XDG_' + n.upper() + '_HOME': str(root / n) for n in ('config', 'data', 'cache', 'state')},
        'TERMINAL_ENV': 'local', 'TERMINAL_LOCAL_PERSISTENT': 'false',
    }
    for key in ('HERMES_HOME', 'TMPDIR', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
        Path(overrides[key]).mkdir(parents=True, exist_ok=True)
    return minimal_environment(root, overrides, passthrough=('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT'))


def source_digest(source, env):
    # git supplies the reviewed tracked set; digest actual working bytes,
    # including link targets, not just the commit claimed by local metadata.
    paths = probe(['git', '-C', source, 'ls-files', '-z'], source, env).split('\0')
    digest = hashlib.sha256()
    count = 0
    for name in sorted(filter(None, paths)):
        path = source / name
        content = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
        count += 1
    return count, digest.hexdigest()


def verify_source(source, pin, env):
    if not (source / '.git').exists():
        raise ValueError('Hermes source checkout missing; run install_runtime.py')
    commit = probe(['git', '-C', source, 'rev-parse', 'HEAD'], source, env)
    if commit != pin['source_commit']:
        raise ValueError('Hermes source commit mismatch')
    if probe(['git', '-C', source, 'rev-parse', pin['release_tag'] + '^{tag}'], source, env) != pin['tag_object']:
        raise ValueError('Hermes release tag object mismatch')
    if probe(['git', '-C', source, 'rev-parse', pin['release_tag'] + '^{commit}'], source, env) != commit:
        raise ValueError('Hermes release tag target mismatch')
    if probe(['git', '-C', source, 'status', '--porcelain', '--untracked-files=normal'], source, env):
        raise ValueError('Hermes source checkout is dirty')
    if digest_file(source / 'uv.lock') != pin['uv_lock_sha256']:
        raise ValueError('Hermes uv.lock identity mismatch')
    count, tree = source_digest(source, env)
    if tree != pin['source_tree_sha256']:
        raise ValueError('Hermes tracked source identity mismatch')
    return {'source_commit': commit, 'source_tree_sha256': tree, 'source_file_count': count,
            'uv_lock_sha256': pin['uv_lock_sha256'], 'version': pin['version']}


def inspect_source(source, pin, env):
    """Identify a Hermes checkout's working bytes BEFORE anything in it runs.

    Unlike verify_source (the installer's strict check of the pinned release),
    any checkout may run: what differs from the pin is listed in
    `pin_mismatches`. A missing or dirty checkout is still refused, because
    untracked or modified files would run without being in the digest.
    """
    if not (source / '.git').exists():
        raise ValueError('Hermes source checkout missing; run hwb harness add hermes')
    commit = probe(['git', '-C', source, 'rev-parse', 'HEAD'], source, env)
    if probe(['git', '-C', source, 'status', '--porcelain', '--untracked-files=normal'], source, env):
        raise ValueError('Hermes source checkout is dirty')
    tags = sorted(filter(None, probe(['git', '-C', source, 'tag', '--points-at', 'HEAD'], source, env).splitlines()))
    release_tag = pin['release_tag'] if pin['release_tag'] in tags else (tags[0] if tags else None)
    tag_object = None
    if release_tag is not None:
        try:
            tag_object = probe(['git', '-C', source, 'rev-parse', release_tag + '^{tag}'], source, env)
        except ValueError:
            tag_object = None  # a lightweight tag has no tag object
    lock = source / 'uv.lock'
    uv_lock = digest_file(lock) if lock.is_file() else None
    count, tree = source_digest(source, env)
    identity = {'source_commit': commit, 'release_tag': release_tag, 'tag_object': tag_object,
                'source_tree_sha256': tree, 'source_file_count': count, 'uv_lock_sha256': uv_lock}
    identity['pin_mismatches'] = [key for key in ('source_commit', 'release_tag', 'tag_object',
                                                  'uv_lock_sha256', 'source_tree_sha256')
                                  if identity[key] != pin[key]]
    return identity


VERSION_PREFIX = 'Hermes Agent v'


def hermes_version(python, source, root, env):
    """The version a Hermes checkout reports, or a refusal if it does not answer like Hermes."""
    probes = ([python, '-c', 'from hermes_cli._startup_fast import print_fast_version_info; print_fast_version_info(check_updates=False)'],
              [python, source / 'hermes', '--version'])
    for argv in probes:
        try:
            text = probe(argv, source, env, timeout=60)
        except ValueError:
            continue
        line = next((l for l in text.splitlines() if l.startswith(VERSION_PREFIX)), None)
        if line:
            return line[len(VERSION_PREFIX):].split()[0], text
    raise ValueError('Hermes version probe failed: the runtime does not answer like Hermes')


def verify_runtime(source, pin, root, uv='uv'):
    """Identify the Hermes runtime and record how it differs from the pin.

    Any Hermes, Python, uv or dependency set may run; the source bytes are
    identified before anything executes. Only a runtime that cannot be
    identified at all is refused.
    """
    env = environment(root)
    identity = inspect_source(source, pin, env)  # BEFORE executing Hermes/Python entry points.
    differences = list(identity.pop('pin_mismatches'))
    uv_path = shutil.which(uv)
    if not uv_path:
        raise ValueError('uv not found; run hwb harness add hermes, or pass --uv')
    uv_version = probe([uv_path, '--version'], root, env)
    if uv_version.split()[:2] != ['uv', pin['uv_version']]:
        differences.append('uv_version')
    python = source / '.venv/bin/python'
    if not python.is_file():
        raise ValueError('Hermes runtime Python missing; run hwb harness add hermes')
    version = probe([python, '-I', '-c', 'import sys; print(".".join(map(str,sys.version_info[:3])))'], root, env)
    if version != pin['python_version']:
        differences.append('python_version')
    extras = pin.get('extras', [])
    if extras not in ([], ['anthropic']):
        raise ValueError('unsupported Hermes dependency extras profile')
    extra_args = [arg for extra in extras for arg in ('--extra', extra)]
    try:
        stdout = probe([uv_path, 'sync', '--frozen', '--check', '--offline', '--no-dev', *extra_args, '--python', python], source, env, timeout=30)
        dependency_check = {'passed': True, 'stdout': stdout}
    except ValueError as error:
        dependency_check = {'passed': False, 'error': str(error)}
        differences.append('dependency_check')
    dependency_check['mode'] = 'frozen offline check; no install'
    identity['optional_extras'] = extras
    if 'anthropic' in extras:
        try:
            sdk_version = probe([python, '-I', '-c', 'import anthropic; print(anthropic.__version__)'], root, env)
        except ValueError:
            sdk_version = None
        if sdk_version != pin['anthropic_version']:
            differences.append('anthropic_version')
        identity['anthropic_sdk_version'] = sdk_version
    hermes, version_text = hermes_version(python, source, root, env)
    if hermes != pin['version']:
        differences.append('version')
    identity.update({'version': hermes, 'python_version': version, 'uv_version': uv_version,
                     'dependency_check': dependency_check, 'source_root': str(source), 'version_probe': version_text,
                     'pinned': {key: pin[key] for key in ('version', 'source_commit', 'python_version', 'uv_version')},
                     'differs_from_pin': differences})
    return python, identity
