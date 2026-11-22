"""Materialize and execute owned Hermes adapter sources."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import sys


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('timeout must be finite and positive')
    return number


def store_runtime():
    """(source checkout, uv path) of the runtime-store Hermes, or (None, None).

    Precedence is --runtime > the workspace's runtime/source > the store. The
    store's default version is preferred, else the newest installed; its uv is
    the one that built its environment.
    """
    from . import runtimes
    try:
        root = runtimes.store_root()
        entry = runtimes.load_catalog()['harnesses']['hermes']
        install = runtimes._default_install(root, 'hermes', entry)
    except (runtimes.RuntimeStoreError, OSError, ValueError, KeyError):
        return None, None
    if install is None:
        return None, None
    record = install['install_record']
    source = record.get('source_root')
    if not source or not Path(source, '.git').exists():
        return None, None
    uv_bin = (record.get('uv') or {}).get('bin')
    uv = Path(uv_bin) / 'uv' if uv_bin else None
    return source, (str(uv) if uv is not None and uv.is_file() else None)


def apply_saved_model(dest, name, builder, native_names):
    """Write saved model `name` into a new workspace's adapter_config.json provider."""
    import importlib.util
    from . import models
    provider = models.provider_for(name, builder)
    spec = importlib.util.spec_from_file_location('owned_provider_auth', dest / 'provider_auth.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate(provider, native_names)
    path = dest / 'adapter_config.json'
    config = json.loads(path.read_text())
    config['provider'] = provider
    path.write_text(json.dumps(config, indent=2) + '\n')
    return provider


def main(argv=None):
    parser = argparse.ArgumentParser(prog='hwb-hermes')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='create a new owned Hermes builder workspace')
    init.add_argument('directory', type=Path)
    init.add_argument('--model', help='saved model name (hwb model add) to use instead of the offline scripted provider')
    for name in ('doctor', 'demo', 'run'):
        child = sub.add_parser(name)
        child.add_argument('target', type=Path, help='config file for run; owned directory otherwise')
        child.add_argument('--runtime', type=Path, help='Hermes source checkout; default owned runtime/source, '
                           'else the one installed with hwb harness add hermes')
        child.add_argument('--uv', help='uv executable; default the one the store Hermes was built with, else uv on PATH')
        child.add_argument('--timeout', type=positive, default=90)
        child.add_argument('--output', type=Path)
        child.add_argument('--workspace-parent', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            dest = args.directory.absolute()
            if dest.exists() or dest.is_symlink():
                raise ValueError('destination already exists: ' + str(dest))
            if args.model:
                from . import models
                models.provider_for(args.model, 'hermes')  # refuse an unknown name before creating anything
            shutil.copytree(Path(__file__).parent / 'hermes_builder', dest,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'runtime'))
            for path in dest.rglob('*'):
                if path.is_file():
                    path.chmod(0o755 if path.suffix == '.sh' else 0o644)
            if args.model:
                provider = apply_saved_model(dest, args.model, 'hermes', {'anthropic', 'openai-api', 'openrouter'})
                print(f"Model: {args.model} ({provider['model']} at {provider['base_url']}, mode {provider['mode']})")
            print(f'Created {dest}\nNext: hwb harness add hermes (if not installed), then hwb-hermes doctor {dest}')
            return 0
        target = args.target.resolve(strict=True)
        config = target if args.command == 'run' else target / 'adapter_config.json'
        script = config.parent / 'adapter.py'
        if not config.is_file() or not script.is_file():
            raise ValueError('owned config/adapter missing; use hwb-hermes init first')
        runtime, uv = args.runtime, args.uv
        if runtime is None and not (config.parent / 'runtime' / 'source').exists():
            runtime, store_uv = store_runtime()
            uv = uv or store_uv
        uv = uv or 'uv'
        uv = str(Path(uv).resolve(strict=True)) if os.sep in uv else uv
        command = [sys.executable, str(script), str(config), '--uv', uv, '--timeout', str(args.timeout)]
        if runtime is not None:
            command.extend(['--runtime', str(Path(runtime).resolve(strict=True))])
        if args.command == 'doctor':
            command.append('--check-only')
        elif args.command == 'demo':
            command.append('--demo')
        if args.workspace_parent is not None:
            command.extend(['--workspace-parent', str(args.workspace_parent.resolve(strict=True))])
        if args.output is not None:
            fd = os.open(args.output.absolute(), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                sys.stdout.flush()
                os.dup2(fd, 1)
            finally:
                os.close(fd)
        os.chdir(config.parent)
        os.execv(sys.executable, command)
    except (ValueError, OSError) as error:
        print(json.dumps({'schema': 'hermes-hwb-builder-error/v0.1', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
