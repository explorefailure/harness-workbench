#!/usr/bin/env python3
"""Explicit, optional source installation; no global Hermes changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from runtime_identity import environment, probe, verify_source


def main():
    root = Path(__file__).resolve().parent
    target = root / 'runtime'
    if target.exists() or target.is_symlink():
        raise ValueError('runtime already exists; preserve it or initialize a new workspace')
    pin = json.loads((root / 'pin.json').read_text())
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('uv not found; install uv==' + pin['uv_version'] + ' in your Workbench environment')
    with tempfile.TemporaryDirectory(prefix='.hwb-hermes-install-', dir=root) as temp:
        env = environment(Path(temp))
        if probe([uv, '--version'], root, env).split()[:2] != ['uv', pin['uv_version']]:
            raise ValueError('uv version mismatch; expected ' + pin['uv_version'])
        target.mkdir()
        source = target / 'source'
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', pin['release_tag'],
            'https://github.com/NousResearch/hermes-agent.git', str(source)],
            check=True, env=env, timeout=180)
        identity = verify_source(source, pin, env)
        # Installer network access is explicit. Runtime/doctor probes stay offline.
        install_env = dict(env)
        install_env.pop('UV_OFFLINE', None)
        install_env['UV_CACHE_DIR'] = str(target / 'uv-cache')
        install_env['UV_PYTHON_INSTALL_DIR'] = str(target / 'python')
        install_env['UV_PYTHON_PREFERENCE'] = 'only-managed'
        # Hermes supports source editable installs, deliberately guards ordinary
        # wheel/sdist builds. Respect that contract; never override its guard.
        subprocess.run([uv, 'sync', '--frozen', '--no-dev', '--python', pin['python_version']],
            cwd=source, env=install_env, check=True, timeout=600)
        (target / 'installation.json').write_text(json.dumps(identity, indent=2) + '\n')
    print('Installed pinned Hermes source/runtime: ' + str(source))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
