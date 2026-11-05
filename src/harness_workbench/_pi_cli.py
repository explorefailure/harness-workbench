"""Builder CLI for the pinned Pi integration; no public Python API.

The adapter is materialized as owned, digestible workload source. It is never
imported into the Workbench CLI or promoted to the cross-subject public API.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import sys


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hwb-pi")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a new owned Pi builder workspace")
    init.add_argument("directory", type=Path)
    init.add_argument("--profile", choices=("baseline", "pi-1.4.0-node-26.13.0"), default="pi-1.4.0-node-26.13.0",
                      help="exact runtime profile; baseline is for historical comparisons only")
    configure = sub.add_parser('configure', help='write a NEW model-backed config using native Pi providers')
    configure.add_argument('directory', type=Path)
    configure.add_argument('--model', required=True)
    configure.add_argument('--provider', help='built-in Pi provider name; otherwise a compatible endpoint')
    configure.add_argument('--base-url')
    configure.add_argument('--api-key-env', help='name of the explicitly selected credential environment variable')
    configure.add_argument('--api', default='openai-completions')
    configure.add_argument('--context-window', type=int, default=32768)
    configure.add_argument('--max-tokens', type=int, default=4096)
    configure.add_argument('--check', action='append', default=[], help='independent check argv as a JSON array; repeatable')
    configure.add_argument('--config-name', default='model_adapter_config.json')
    for name, description in (
        ("doctor", "check the pinned Pi/Node runtime without running a task"),
        ("demo", "run the offline coding repair and independent outcome check"),
        ("run", "capture a workload; success does not certify task correctness"),
    ):
        child = sub.add_parser(name, help=description)
        child.add_argument("target", type=Path, help="config JSON for run; workspace directory otherwise")
        child.add_argument("--pi", default="pi", help="Pi launcher (Node must also be on PATH)")
        child.add_argument("--timeout", type=_positive_seconds, default=30.0)
        child.add_argument("--output", type=Path, help="write JSON to a NEW file; never overwrite evidence")
        if name != "doctor":
            child.add_argument("--workspace-parent", type=Path, help="existing directory for retained evidence")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            destination = args.directory.absolute()
            # Reject existing directories, including empty ones, and dangling
            # links. No partial merge and no force mode for builder evidence.
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"destination already exists: {destination}")
            source = Path(__file__).parent / "pi_builder"
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules"))
            if args.profile != "baseline":
                shutil.copyfile(destination / ("pin-" + args.profile + ".json"), destination / "pin.json")
            for path in destination.rglob("*"):
                if path.is_file():
                    path.chmod(0o755 if path.suffix == ".sh" else 0o644)
            print(f"Created {destination}\nNext: hwb-pi doctor {destination}")
            return 0

        if args.command == 'configure':
            import importlib.util
            root = args.directory.resolve(strict=True)
            if not root.is_dir() or Path(args.config_name).name != args.config_name or args.config_name in ('', '.', '..'):
                raise ValueError('config-name must be a new filename inside an owned directory')
            spec = importlib.util.spec_from_file_location('owned_pi_provider_config', root / 'provider_config.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            provider = {'mode': 'native' if args.provider else 'compatible', 'model': args.model}
            if args.provider:
                if args.api != 'openai-completions' or args.context_window != 32768 or args.max_tokens != 4096:
                    raise ValueError('native providers use their catalog API/context limits')
                provider['name'] = args.provider
            else:
                provider.update(api=args.api, context_window=args.context_window, max_tokens=args.max_tokens)
            if args.base_url is not None:provider['base_url'] = args.base_url
            if args.api_key_env is not None:provider['api_key_env'] = args.api_key_env
            module.validate(provider)
            config = json.loads((root / 'coding_adapter_config.json').read_text())
            config['provider'] = provider
            config['extensions'] = []
            config['pi_arguments'] = ['--mode', 'json', '--no-session', '--no-extensions',
                '--no-skills', '--no-prompt-templates', '--no-context-files', '--no-approve',
                '--offline', '--tools', 'read,edit,bash']
            config['network_mode'] = 'explicit model/provider requests; automatic Pi network activity disabled'
            config['inputs'] = [p for p in config['inputs'] if p not in ('coding_adapter_config.json', 'coding_provider.ts')]
            config['inputs'].extend([args.config_name, 'provider_config.py'])
            if args.check:
                spec = importlib.util.spec_from_file_location('owned_task_checks', root / 'task_checks.py')
                checks_module = importlib.util.module_from_spec(spec);spec.loader.exec_module(checks_module)
                config['checks'] = [{'id': 'check-' + str(i+1), 'argv': json.loads(raw)} for i, raw in enumerate(args.check)]
                checks_module.validate(config['checks'])
                config['inputs'].append('task_checks.py')
            destination = root / args.config_name
            with destination.open('x') as file:
                file.write(json.dumps(config, indent=2) + '\n')
            destination.chmod(0o600)
            print('Created ' + str(destination) + '\nNext: hwb-pi run ' + str(destination) + ' --pi YOUR_PINNED_PI_LAUNCHER')
            return 0

        target = args.target.resolve(strict=True)
        config = target if args.command == "run" else target / "coding_adapter_config.json"
        if not config.is_file():
            raise ValueError(f"config is not a file: {config}")
        script = config.parent / ("coding_runner.py" if args.command == "demo" else "adapter.py")
        if not script.is_file():
            raise ValueError(f"owned adapter script not found: {script}; use hwb-pi init first")
        command = [sys.executable, str(script)]
        if args.command != "demo":
            command.append(str(config))
        if args.command == "doctor":
            command.append("--check-only")
        command.extend(["--pi", args.pi, "--timeout", str(args.timeout)])
        if getattr(args, "workspace_parent", None) is not None:
            parent = args.workspace_parent.resolve(strict=True)
            if not parent.is_dir():
                raise ValueError("workspace-parent must be a directory")
            command.extend(["--workspace-parent", str(parent)])
        # Resolve --pi before changing cwd so relative launcher paths retain
        # the caller's meaning. Bare names still resolve through PATH.
        if os.sep in args.pi:
            command[command.index("--pi") + 1] = str(Path(args.pi).resolve(strict=True))
        if args.output is not None:
            output = args.output.absolute()
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                sys.stdout.flush()
                os.dup2(fd, 1)
            finally:
                os.close(fd)
        if args.command == "demo":
            # The reference oracle intentionally checks modes as well as bytes.
            os.umask(0o022)
        os.chdir(config.parent)
        # Replace the wrapper so cancellation reaches the adapter's existing
        # signal handling rather than leaving it behind a parent process.
        os.execv(sys.executable, command)
    except (OSError, ValueError) as error:
        print(json.dumps({"schema": "pi-hwb-builder-error/v0.1", "error": str(error)}), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
