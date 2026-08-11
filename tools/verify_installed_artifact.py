#!/usr/bin/env python3
"""Install one release artifact into a clean venv and exercise its public UX."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(argv))
    try:
        return subprocess.run(
            argv, cwd=cwd, env=env, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(error.stdout, end="")
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    if not artifact.is_file():
        raise SystemExit(f"artifact does not exist: {artifact}")

    with tempfile.TemporaryDirectory(prefix="hwb-artifact-") as raw:
        work = Path(raw)
        environment = work / "venv"
        # Invoke the same public venv interface a releaser uses; failures stay
        # visible instead of being hidden inside EnvBuilder's ensurepip call.
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
        bindir = environment / "bin"
        python = str(bindir / "python")
        hwb = str(bindir / "hwb")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONNOUSERSITE"] = "1"

        run([python, "-m", "pip", "install", "--disable-pip-version-check", str(artifact)], cwd=work, env=env)
        run([python, "-m", "pip", "check"], cwd=work, env=env)
        version_check = (
            "import importlib.metadata as m, importlib.util, harness_workbench as p; "
            "assert p.__version__ == m.version('harness-workbench'); "
            "assert importlib.util.find_spec('hwb') is None; "
            "assert 'site-packages' in p.__file__; "
            "print(p.__version__)"
        )
        run([python, "-c", version_check], cwd=work, env=env)
        run([hwb, "--help"], cwd=work, env=env)
        run([python, "-m", "harness_workbench", "--help"], cwd=work, env=env)

        spec = work / "hello.json"
        spec.write_text(json.dumps({
            "schema": "hwbspec/v0.1",
            "features": [],
            "steps": [{"id": "01", "argv": ["/bin/echo", "hello"]}],
        }), encoding="utf-8")
        completed = run([hwb, "run", spec.name], cwd=work, env=env)
        fields = completed.stdout.split()
        if len(fields) < 5 or "completed" not in fields:
            raise SystemExit("installed first run did not report a completed harness run")
        run_id = fields[0]
        listed = run([hwb, "ls"], cwd=work, env=env)
        if run_id not in listed.stdout:
            raise SystemExit("installed first run is absent from hwb ls")
        run([hwb, "show", run_id], cwd=work, env=env)
        run([hwb, "verify", run_id], cwd=work, env=env)
        print(f"verified installed artifact {artifact.name} with run {run_id}")


if __name__ == "__main__":
    main()
