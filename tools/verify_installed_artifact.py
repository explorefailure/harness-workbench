#!/usr/bin/env python3
"""Install one artifact and exercise its public UX and offline subject tree."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


PINNED_BUILD_BACKEND = "83.0.0"


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

        installable = prepare_installable_artifact(artifact, work, env)
        run([
            python, "-m", "pip", "install", "--disable-pip-version-check",
            "--no-index", "--no-deps", str(installable),
        ], cwd=work, env=env)
        run([python, "-m", "pip", "check"], cwd=work, env=env)
        version_check = (
            "import importlib.metadata as m, importlib.util, harness_workbench as p; "
            "assert p.__version__ == m.version('harness-workbench'); "
            "assert importlib.util.find_spec('hwb') is None; "
            "assert 'site-packages' in p.__file__; "
            "print(p.__version__)"
        )
        run([python, "-c", version_check], cwd=work, env=env)
        version = run([hwb, "--version"], cwd=work, env=env)
        if version.stdout.strip() != "hwb " + artifact_version(python, work, env):
            raise SystemExit("hwb --version disagrees with installed metadata")
        run([hwb, "--help"], cwd=work, env=env)
        run([python, "-m", "harness_workbench", "--help"], cwd=work, env=env)
        verify_materialized_subjects(hwb, python, work, env)

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


def prepare_installable_artifact(
    artifact: Path, work: Path, env: dict[str, str]
) -> Path:
    """Turn an sdist into a wheel with the caller's pinned backend, offline.

    A clean venv must not resolve ``build-system.requires`` from an index: that
    would make the verification step network-dependent and silently replace
    the backend pin used to create the release artifacts.  The release
    environment already owns the exact backend, so build there without PEP 517
    isolation, forbid index access, then install only the resulting wheel in
    the clean venv.
    """
    if not artifact.name.endswith(".tar.gz"):
        return artifact
    backend = importlib.metadata.version("setuptools")
    if backend != PINNED_BUILD_BACKEND:
        raise SystemExit(
            "source distribution verification requires setuptools=="
            f"{PINNED_BUILD_BACKEND}, found {backend}"
        )
    wheelhouse = work / "sdist-wheel"
    wheelhouse.mkdir()
    build_env = dict(env)
    build_env["PIP_NO_INDEX"] = "1"
    run([
        sys.executable, "-m", "pip", "wheel",
        "--disable-pip-version-check", "--no-index", "--no-deps",
        "--no-build-isolation", "--wheel-dir", str(wheelhouse),
        str(artifact),
    ], cwd=work, env=build_env)
    wheels = sorted(wheelhouse.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(
            f"source distribution produced {len(wheels)} wheels, expected one"
        )
    return wheels[0]


def verify_materialized_subjects(
    hwb: str, python: str, work: Path, env: dict[str, str]
) -> None:
    """Prove the installed artifact ships a self-consistent offline tree."""
    destination = work / "subjects"
    run([hwb, "subjects", "--into", str(destination)], cwd=work, env=env)
    run(
        [
            python,
            "-m", "unittest", "discover",
            "-s", str(destination),
            "-p", "test_experiment.py",
            "-v",
        ],
        cwd=work,
        env=env,
    )


def artifact_version(python: str, cwd: Path, env: dict[str, str]) -> str:
    completed = run(
        [python, "-c", "import importlib.metadata as m; "
         "print(m.version('harness-workbench'))"],
        cwd=cwd,
        env=env,
    )
    return completed.stdout.strip()


if __name__ == "__main__":
    main()
