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
import pathlib
from typing import Any

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


def experiment_document(
    subject: str,
    workload: str,
    variant: str | None,
    capture: dict[str, Any],
) -> dict[str, Any]:
    """Project one adapter envelope into the public experiment document.

    The outcome is deliberately tri-state. Python's `and` returns an operand,
    which is the truth table wanted here: a failed adapter proves the combined
    result false, while a valid adapter paired with an unknown outcome remains
    unknown. Keeping this projection pure makes every producer-emittable state
    testable without invoking an external harness.
    """
    adapter_passed = capture["verdict"]["passed"]
    outcome_passed = capture["outcome"]["passed"]
    evaluable = capture["outcome"].get("evaluable", True)
    forwarded = list(capture["capture"]["forwarded_signals"])
    oracle_evidence = capture.get("oracle_evidence")
    if isinstance(oracle_evidence, dict):
        for name in ("initial_test", "final_test"):
            process = oracle_evidence.get(name)
            if isinstance(process, dict):
                signals = process.get("forwarded_signals")
                if isinstance(signals, list):
                    forwarded.extend(signals)
    interrupted = bool(forwarded)
    status = exit_status(adapter_passed, interrupted, evaluable)
    return {
        "schema": "cross-harness-experiment-run/v0.1",
        "subject": subject,
        "workload": workload,
        "variant": variant,
        "verdict": {
            "passed": adapter_passed and outcome_passed,
            "adapter_passed": adapter_passed,
            "outcome_passed": outcome_passed,
            "evaluable": evaluable,
            "interrupted": interrupted,
            "status": status,
        },
        "adapter": capture,
    }


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
    parser.add_argument(
        "--record", default=None, metavar="PATH",
        help="also write the record here; the run is the only place it exists",
    )
    args = parser.parse_args()

    def retain(document: dict) -> None:
        """Write the record somewhere that outlives the terminal it ran in.

        A subject run costs real money and is not reproducible after the fact:
        the workspace is a temporary directory that deletes itself, and the
        record printed below is the only artefact the run ever produces. The
        first full containment matrix -- ten arms, five harnesses, both
        variants -- was measured this way and then existed nowhere, so its
        results could be quoted but never checked. For a tree whose whole
        doctrine is that a positive receipt decides what happened, a headline
        result backed by no retained receipt is the wrong shape.

        A run that FAILED is retained too. The instrumentation failures on this
        tree were the expensive ones to rediscover, and they are exactly the
        runs somebody would otherwise not think to keep.

        Optional rather than mandatory because a smoke run genuinely does not
        need a file. Named on the command line so that retaining one is a flag
        and not a shell redirect somebody has to remember.
        """
        if args.record is None:
            return
        destination = pathlib.Path(args.record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    try:
        capture = adapters.capture(
            args.subject,
            args.workload,
            variant=args.variant,
            timeout=SUBJECT_TIMEOUT_SECONDS[args.subject],
        )
    except (adapters.AdapterError, OSError, TypeError, ValueError) as error:
        failure = {
            "schema": "cross-harness-experiment-run/v0.1",
            "subject": args.subject,
            "workload": args.workload,
            "variant": args.variant,
            "error": str(error),
        }
        print(json.dumps(failure, sort_keys=True))
        retain(failure)
        return 2
    document = experiment_document(
        args.subject, args.workload, args.variant, capture
    )
    print(json.dumps(document, sort_keys=True))
    retain(document)
    return document["verdict"]["status"]


if __name__ == "__main__":
    raise SystemExit(main())
