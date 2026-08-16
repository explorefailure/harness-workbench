#!/usr/bin/env python3
"""Run one external harness and apply its independent effect oracle.

EXIT CODES ARE THE WORKBENCH'S, NOT THIS SCRIPT'S. `hwb` already decided what
a status means -- "a harness that worked exits 0, whatever the steps did", and
"a non-zero workload exit is recorded data, not a harness error". An adapter is
a harness over a subject, so the same rule applies one level down:

    0  the subject was measured validly, whatever it did about the task
    1  the measurement is not trustworthy -- a bound fired, required evidence
       was missing, the process group leaked
    2  nothing could be run at all -- pin mismatch, missing executable
    3  refusal: the run was interrupted, or the guard workload left no
       startup receipt, so no verdict here is readable

Whether the subject DID the task is `outcome.passed` in the record, and it is
deliberately not in this status. Blending the two was the old behaviour, and
it broke the one thing every workbench wrap can see: `retry` reads
`exit == 0`, so a subject that captured perfectly and declined the task looked
identical to one whose instrumentation failed, and got re-run at full cost.

3 is a refusal in the sense `hwb diff` already uses it -- "a script must never
be able to read a refusal as a difference". An interrupted run is not a
failing run; reading it as one would attribute an operator's Ctrl-C to a
harness.
"""
from __future__ import annotations

import argparse
import json

import adapters

SUBJECT_TIMEOUT_SECONDS = {
    "claude": 120,
    "codex": 120,
    "deepseek": 240,
    "hermes": 120,
    "pi": 240,
}


def exit_status(
    adapter_passed: bool, interrupted: bool, evaluable: bool = True
) -> int:
    """The status alone, so it can be tested without five installed harnesses.

    Note what is NOT a parameter: whether the subject did the task. That is the
    whole point -- the outcome cannot reach this decision even by accident.

    `evaluable` is the guard workload's third state. A run whose interceptor
    left no startup receipt cannot be read as a block OR as a leak: nobody can
    say the guard was ever installed, so a missing effect may only mean the
    model never tried. That is a refusal, and it shares 3 with an interrupted
    run because both mean the same thing -- do not read a verdict here.
    """
    if interrupted or not evaluable:
        return 3
    return 0 if adapter_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--subject",
        required=True,
        choices=("claude", "codex", "deepseek", "hermes", "pi"),
    )
    parser.add_argument("--workload", default="write", choices=tuple(adapters.WORKLOADS))
    parser.add_argument("--variant", default=None, choices=adapters.GUARD_VARIANTS,
                        help="guard workload only: which arm of the pair this is")
    args = parser.parse_args()
    try:
        capture = adapters.capture(
            args.subject,
            args.workload,
            variant=args.variant,
            timeout=SUBJECT_TIMEOUT_SECONDS[args.subject],
        )
    except (adapters.AdapterError, OSError, TypeError, ValueError) as error:
        print(json.dumps({
            "schema": "cross-harness-experiment-run/v0.1",
            "subject": args.subject,
            "workload": args.workload,
            "variant": args.variant,
            "error": str(error),
        }, sort_keys=True))
        return 2
    adapter_passed = capture["verdict"]["passed"]
    outcome_passed = capture["outcome"]["passed"]
    # Absent for every workload but `guard`, where it is the whole point.
    evaluable = capture["outcome"].get("evaluable", True)
    interrupted = bool(capture["capture"]["forwarded_signals"])
    status = exit_status(adapter_passed, interrupted, evaluable)
    print(json.dumps({
        "schema": "cross-harness-experiment-run/v0.1",
        "subject": args.subject,
        "workload": args.workload,
        "variant": args.variant,
        "verdict": {
            # Kept, and kept named. Readers that want the conjunction can still
            # have it; what they can no longer do is get it from $? and mistake
            # a declined task for a broken measurement.
            "passed": adapter_passed and outcome_passed,
            "adapter_passed": adapter_passed,
            "outcome_passed": outcome_passed,
            "evaluable": evaluable,
            "interrupted": interrupted,
            "status": status,
        },
        "adapter": capture,
    }, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
