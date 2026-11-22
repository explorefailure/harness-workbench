"""Materialize and execute owned OpenCode adapter sources."""
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
    parser = argparse.ArgumentParser(prog='hwb-opencode')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='create a new owned OpenCode builder workspace')
    init.add_argument('directory', type=Path)
    init.add_argument('--model', help='saved model name (hwb model add) to use instead of the offline scripted provider')
    for name in ('doctor', 'demo', 'run'):
        child = sub.add_parser(name)
        child.add_argument('target', type=Path, help='config file for run; owned directory otherwise')
        child.add_argument('--opencode', default='opencode')
        child.add_argument('--timeout', type=positive, default=60)
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
                models.provider_for(args.model, 'opencode')  # refuse an unknown name before creating anything
            shutil.copytree(Path(__file__).parent / 'opencode_builder', dest,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'runtime'))
            for path in dest.rglob('*'):
                if path.is_file():
                    path.chmod(0o755 if path.suffix == '.sh' else 0o644)
            if args.model:
                provider = apply_saved_model(dest, args.model, 'opencode', {'anthropic', 'openai', 'google', 'openrouter'})
                print(f"Model: {args.model} ({provider['model']} at {provider['base_url']}, mode {provider['mode']})")
            print(f'Created {dest}\nNext: read README.md, install the pinned runtime, then hwb-opencode doctor {dest}')
            return 0
        target = args.target.resolve(strict=True)
        config = target if args.command == 'run' else target / 'adapter_config.json'
        script = config.parent / 'adapter.py'
        if not config.is_file() or not script.is_file():
            raise ValueError('owned config/adapter missing; use hwb-opencode init first')
        launcher = args.opencode
        if os.sep in launcher:
            launcher = str(Path(launcher).resolve(strict=True))
        command = [sys.executable, str(script), str(config), '--opencode', launcher, '--timeout', str(args.timeout)]
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
        # An OpenCode installed with `hwb harness add opencode` is found when
        # none is on PATH; a PATH OpenCode still wins.
        from . import runtimes
        try:
            os.environ.update(runtimes.subject_environment(dict(os.environ))["environment"])
        except (runtimes.RuntimeStoreError, OSError, ValueError):
            pass
        os.execv(sys.executable, command)
    except (ValueError, OSError) as error:
        print(json.dumps({'schema': 'opencode-hwb-builder-error/v0.1', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
