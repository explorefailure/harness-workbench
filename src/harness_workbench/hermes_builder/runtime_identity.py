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


def verify_runtime(source, pin, root, uv='uv'):
    env = environment(root)
    identity = verify_source(source, pin, env)  # BEFORE executing Hermes/Python entry points.
    uv_path = shutil.which(uv)
    if not uv_path:
        raise ValueError('uv not found; install the optional pinned runtime tool')
    uv_version = probe([uv_path, '--version'], root, env)
    if uv_version.split()[:2] != ['uv', pin['uv_version']]:
        raise ValueError('uv version mismatch')
    python = source / '.venv/bin/python'
    if not python.is_file():
        raise ValueError('Hermes runtime Python missing; run install_runtime.py')
    version = probe([python, '-I', '-c', 'import sys; print(".".join(map(str,sys.version_info[:3])))'], root, env)
    if version != pin['python_version']:
        raise ValueError('Hermes Python version mismatch: ' + version)
    dependency_check = probe([uv_path, 'sync', '--frozen', '--check', '--offline', '--no-dev', '--python', python], source, env, timeout=30)
    version_text = probe([python, '-c', 'from hermes_cli._startup_fast import print_fast_version_info; print_fast_version_info(check_updates=False)'], source, env)
    if not version_text.startswith('Hermes Agent v' + pin['version'] + ' '):
        raise ValueError('Hermes version probe mismatch')
    identity.update({'python_version': version, 'uv_version': uv_version, 'dependency_check': {'passed': True, 'stdout': dependency_check, 'mode': 'frozen offline check; no install'},
                     'source_root': str(source), 'version_probe': version_text})
    return python, identity
