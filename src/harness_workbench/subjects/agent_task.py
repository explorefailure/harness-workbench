#!/usr/bin/env python3
"""Plan or execute the finite offline declarative-agent conformance path."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_task_offline import run_offline_campaign
from agent_task_phase_review import review_fake_smoke_checkpoint
from agent_task_live_plan import generate_live_plan
from agent_task_schema import SUBJECTS


def plan(destination: Path | None) -> dict:
    return {
        "schema": "agent-task-offline-plan/v0.1",
        "live": False,
        "network_calls_authorized": 0,
        "paid_provider_calls_authorized": 0,
        "offline_fake_provider_invocations": 5,
        "subjects": list(SUBJECTS),
        "destination": str(destination.resolve()) if destination is not None else None,
        "destination_must_not_exist": True,
        "workspaces_per_subject": 3,
        "workbench_stores": 5,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-destination", type=Path)
    parser.add_argument(
        "--live-plan-destination", type=Path,
        help="display the exact digest-bound live preview; authorizes zero calls",
    )
    parser.add_argument(
        "--usage-snapshot", type=Path,
        help="inject a freshly retained usage snapshot into --live-plan-destination",
    )
    parser.add_argument(
        "--review-smoke-destination", type=Path,
        help="offline-review one retained fake smoke checkpoint; makes zero calls",
    )
    parser.add_argument(
        "--run-offline", action="store_true",
        help="execute the five deterministic fake routes; never makes network calls",
    )
    args = parser.parse_args()
    selected = sum((
        args.live_plan_destination is not None,
        args.run_offline,
        args.review_smoke_destination is not None,
    ))
    if selected > 1:
        parser.error(
            "live plan, offline execution, and smoke review are mutually exclusive"
        )
    if args.review_smoke_destination is not None:
        if args.offline_destination is not None or args.usage_snapshot is not None:
            parser.error("smoke review does not accept execution or usage options")
        document = review_fake_smoke_checkpoint(
            args.review_smoke_destination,
        )
        print(json.dumps(document, sort_keys=True, indent=2))
        return 0 if document["passed"] else 1
    if args.live_plan_destination is not None:
        if args.offline_destination is not None:
            parser.error("live plan and offline execution options are mutually exclusive")
        usage = None
        if args.usage_snapshot is not None:
            usage = json.loads(args.usage_snapshot.read_text(encoding="utf-8"))
        document = generate_live_plan(
            args.live_plan_destination, usage_snapshot=usage
        )
        print(json.dumps(document, sort_keys=True, indent=2))
        return 0
    if args.usage_snapshot is not None:
        parser.error("--usage-snapshot requires --live-plan-destination")
    document = plan(args.offline_destination)
    if not args.run_offline:
        print(json.dumps(document, sort_keys=True, indent=2))
        return 0
    if args.offline_destination is None:
        parser.error("--run-offline requires --offline-destination")
    report = run_offline_campaign(args.offline_destination.resolve())
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
