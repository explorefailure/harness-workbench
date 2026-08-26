#!/usr/bin/env python3
"""Plan or execute bounded one-draw live adapter recertification."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import doctor
from harness_workbench.canon import digest_file
from harness_workbench.capture import run_bounded
import runner


SCHEMA = "cross-harness-recertification-report/v0.1"
MAX_DRAWS = 3


def build_plan(subjects: tuple[str, ...], workload: str, draws: int) -> dict[str, Any]:
    if not subjects or any(subject not in doctor.SUBJECTS for subject in subjects):
        raise ValueError("recertification requires known subjects")
    if workload not in {"write", "repair"}:
        raise ValueError("recertification supports write or repair")
    if type(draws) is not int or not 1 <= draws <= MAX_DRAWS:
        raise ValueError(f"draws must be between 1 and {MAX_DRAWS}")
    return {
        "schema": SCHEMA,
        "live": False,
        "live_subject_runs_planned": len(subjects) * draws,
        "model_calls_authorized": 0,
        "workload": workload,
        "draws": draws,
        "subjects": list(subjects),
        "commands": [
            [
                "python3.11",
                "runner.py",
                "--subject",
                subject,
                "--workload",
                workload,
            ]
            for subject in subjects
            for _ in range(draws)
        ],
    }


def execute(plan: dict[str, Any], record_dir: Path) -> tuple[dict[str, Any], int]:
    record_dir = record_dir.resolve()
    record_dir.mkdir(parents=True, exist_ok=True)
    report_path = record_dir / "recertification-report.json"
    if report_path.exists():
        raise ValueError(f"refusing to overwrite {report_path}")
    preflight = doctor.report(tuple(plan["subjects"]))
    blocked = [
        row for row in preflight["subjects"]
        if row["status"] in {"pin_drift", "schema_drift", "auth_missing"}
    ]
    if blocked:
        names = ", ".join(f"{row['subject']}={row['status']}" for row in blocked)
        raise ValueError(f"live recertification blocked by doctor: {names}")
    results = []
    command_index = 0
    stop = False
    for subject in plan["subjects"]:
        for draw in range(1, plan["draws"] + 1):
            destination = record_dir / f"{subject}-{plan['workload']}-{draw}.json"
            if destination.exists():
                raise ValueError(f"refusing to overwrite {destination}")
            argv = list(plan["commands"][command_index]) + [
                "--record", str(destination)
            ]
            command_index += 1
            bounded = run_bounded(
                argv,
                cwd=doctor.adapters.HERE,
                env=dict(os.environ),
                timeout=runner.SUBJECT_TIMEOUT_SECONDS[subject] + 30,
                stdout_limit=4 * 1024 * 1024,
                stderr_limit=1024 * 1024,
                termination_grace=3.0,
            )
            document: dict[str, Any] | None = None
            if destination.is_file():
                try:
                    document = json.loads(destination.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    document = None
            verdict = document.get("verdict", {}) if isinstance(document, dict) else {}
            passed = bool(
                bounded.returncode == 0
                and bounded.termination_reason is None
                and not bounded.group_alive_after_cleanup
                and isinstance(document, dict)
                and document.get("schema") == "cross-harness-experiment-run/v0.1"
                and document.get("subject") == subject
                and document.get("workload") == plan["workload"]
                and document.get("variant") is None
                and verdict.get("adapter_passed") is True
                and verdict.get("outcome_passed") is True
            )
            results.append({
                "subject": subject,
                "draw": draw,
                "passed": passed,
                "process_returncode": bounded.returncode,
                "termination_reason": bounded.termination_reason,
                "record": destination.name if destination.is_file() else None,
                "record_sha256": (
                    digest_file(destination) if destination.is_file() else None
                ),
            })
            if not passed:
                stop = True
                break
        if stop:
            break
    report = {
        **plan,
        "live": True,
        "model_calls_authorized": len(results),
        "live_subject_runs_started": len(results),
        "preflight": preflight,
        "passed": all(row["passed"] for row in results),
        "results": results,
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report, 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", action="append", choices=doctor.SUBJECTS)
    parser.add_argument("--workload", default="repair", choices=("write", "repair"))
    parser.add_argument("--draws", type=int, default=1)
    parser.add_argument(
        "--live", action="store_true",
        help="execute the plan; without this flag no subject is run",
    )
    parser.add_argument(
        "--record-dir", type=Path,
        help="required with --live; must not contain a prior report",
    )
    args = parser.parse_args()
    try:
        plan = build_plan(tuple(args.subject or doctor.SUBJECTS), args.workload, args.draws)
        if not args.live:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        if args.record_dir is None:
            raise ValueError("--record-dir is required with --live")
        report, status = execute(plan, args.record_dir)
    except (OSError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
