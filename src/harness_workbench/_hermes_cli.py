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


def main(argv=None):
    parser = argparse.ArgumentParser(prog='hwb-hermes')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='create a new owned Hermes builder workspace')
    init.add_argument('directory', type=Path)
    for name in ('doctor', 'demo', 'run'):
        child = sub.add_parser(name)
        child.add_argument('target', type=Path, help='config file for run; owned directory otherwise')
        child.add_argument('--runtime', type=Path, help='pinned Hermes source checkout; default owned runtime/source')
        child.add_argument('--uv', default='uv')
        child.add_argument('--timeout', type=positive, default=90)
        child.add_argument('--output', type=Path)
        child.add_argument('--workspace-parent', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            dest = args.directory.absolute()
            if dest.exists() or dest.is_symlink():
                raise ValueError('destination already exists: ' + str(dest))
            shutil.copytree(Path(__file__).parent / 'hermes_builder', dest,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'runtime'))
            for path in dest.rglob('*'):
                if path.is_file():
                    path.chmod(0o755 if path.suffix == '.sh' else 0o644)
            print(f'Created {dest}\nNext: read README.md, install the pinned runtime, then hwb-hermes doctor {dest}')
            return 0
        target = args.target.resolve(strict=True)
        config = target if args.command == 'run' else target / 'adapter_config.json'
        script = config.parent / 'adapter.py'
        if not config.is_file() or not script.is_file():
            raise ValueError('owned config/adapter missing; use hwb-hermes init first')
        uv = str(Path(args.uv).resolve(strict=True)) if os.sep in args.uv else args.uv
        command = [sys.executable, str(script), str(config), '--uv', uv, '--timeout', str(args.timeout)]
        if args.runtime is not None:
            command.extend(['--runtime', str(args.runtime.resolve(strict=True))])
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
