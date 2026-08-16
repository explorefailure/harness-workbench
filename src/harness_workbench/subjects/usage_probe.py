#!/usr/bin/env python3
"""Read the gateway's own usage counters, and refuse to spend past a line.

WHY THIS IS NOT IN THE WORKBENCH. `harness-workbench-user-problems-and-
experiment-ideas` puts "paid-API resource-budget enforcement" outside the
zero-dependency base, in an external adapter. This is that adapter: it knows
one vendor's URL and one vendor's JSON, which is exactly the knowledge core
must not acquire. Nothing under `src/harness_workbench/` imports it.

WHAT IT MEASURES, AND WHAT IT REFUSES TO CLAIM. The endpoint reports three
windows as integer PERCENTAGES. The published Go plan prices those windows at
$12/5h, $30/week and $60/month, so it is tempting to multiply and print
dollars. Do not: a reading of 2% weekly was observed against $0.21 of actual
reported spend, where the multiplication says $0.60 and a $0.21-at-2% window
would have to be a $10.50 cap rather than the published $30. The percentages
and the console's dollars do not reconcile, and a number that looks like money
and is not is worse than a percentage that admits what it is.

So this reports percentages and DELTAS of percentages. A delta across a single
run is the honest measurement -- it needs no conversion, and it is robust to
whatever the absolute scale turns out to mean. Provider counters are adapter
observations; the run ledger stays canonical.

UNKNOWN IS NOT SAFE. If the counter cannot be read, the gate REFUSES rather
than allowing. A budget check that fails open is not a budget check; it is a
delay before the same overspend, and it produces the clean-looking run that
absence-of-error always produces.

Exit codes are the Workbench's, as everywhere else in this tree:
    0  read succeeded and every window is under its declared line
    1  a window is over its line -- a real negative verdict
    2  nothing could be run: no key, unusable configuration
    3  refusal: the counters could not be read, so the state is UNKNOWN
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
WINDOWS = ("rolling", "weekly", "monthly")
KEY_ENV = "HWB_OPENCODE_KEY"
# A plain descriptive agent. The endpoint sits behind a bot filter that rejects
# urllib's default, and impersonating a browser to get past it would be a lie
# told to someone else's infrastructure.
USER_AGENT = "harness-workbench/0.1 (+usage probe)"
SCHEMA = "cross-harness-usage-snapshot/v0.1"


class ProbeError(RuntimeError):
    """The probe cannot be performed without guessing."""


def active_profile() -> tuple[str, dict[str, Any]]:
    """The profile `model_selection.json` selects, so the URL is never a guess."""
    selection = json.loads(
        (HERE / "model_selection.json").read_text(encoding="utf-8")
    )
    name = str(selection["active"])
    try:
        return name, selection["profiles"][name]
    except KeyError as error:
        raise ProbeError(f"model_selection.json has no profile {name!r}") from error


def read_usage(base_url: str, key: str, *, timeout: float = 15.0) -> dict[str, Any]:
    """GET <base_url>/usage with the key in a header, never in a URL or argv."""
    request = urllib.request.Request(
        base_url.rstrip("/") + "/usage",
        headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise ProbeError(f"usage endpoint returned {error.code}") from error
    except Exception as error:  # network, DNS, TLS, timeout
        raise ProbeError(f"usage endpoint unreachable: {error}") from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ProbeError(f"usage endpoint did not return JSON: {error.msg}") from error
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise ProbeError("usage payload has no 'usage' object")
    return usage


def snapshot(reader: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
    """One positive reading: every window, its percent, and when it resets."""
    if reader is None:
        profile_name, profile = active_profile()
        if profile.get("kind") != "gateway":
            # Local profiles cost nothing and have no counters. Saying so beats
            # returning zeros that look like a successful gateway reading.
            return {
                "schema": SCHEMA,
                "read_at": _now(),
                "profile": profile_name,
                "metered": False,
                "windows": {},
            }
        key = os.environ.get(KEY_ENV)
        if not key:
            raise ProbeError(f"{KEY_ENV} is not set in the environment")
        base_url = str(profile["base_url"])
        usage = read_usage(base_url, key)
        name = profile_name
    else:
        usage = reader()
        name = "injected"

    windows: dict[str, Any] = {}
    for window in WINDOWS:
        entry = usage.get(window)
        if not isinstance(entry, dict) or not isinstance(entry.get("percent"), int):
            raise ProbeError(f"usage payload has no integer percent for {window!r}")
        windows[window] = {
            "percent": entry["percent"],
            "status": entry.get("status"),
            "resets_at": entry.get("resetsAt"),
        }
    return {
        "schema": SCHEMA,
        "read_at": _now(),
        "profile": name,
        "metered": True,
        "windows": windows,
    }


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What one run consumed, in percentage points per window.

    Reported without conversion to money. A window that RESET between the two
    readings is named as such rather than reported as a negative delta, because
    "usage went down" is not a thing a run can do and a reader would have to
    guess what it meant.
    """
    out: dict[str, Any] = {}
    for window in WINDOWS:
        start = before.get("windows", {}).get(window)
        end = after.get("windows", {}).get(window)
        if not start or not end:
            out[window] = {"points": None, "note": "window missing from a reading"}
            continue
        points = end["percent"] - start["percent"]
        if points < 0 or start.get("resets_at") != end.get("resets_at"):
            out[window] = {
                "points": None,
                "note": "window reset between readings; delta is not measurable",
            }
        else:
            out[window] = {"points": points}
    return out


def gate(reading: dict[str, Any], limits: dict[str, int]) -> tuple[bool, list[str]]:
    """Is every declared window still under its line?

    Only windows the caller NAMED are checked. A limit nobody declared is not a
    limit of zero, and silently enforcing one would stop runs for a rule that
    was never stated.
    """
    reasons: list[str] = []
    if not reading.get("metered"):
        return True, ["profile is not metered; nothing to gate"]
    for window, ceiling in sorted(limits.items()):
        entry = reading.get("windows", {}).get(window)
        if entry is None:
            reasons.append(f"{window}: no reading, cannot be gated")
            continue
        if entry["percent"] >= ceiling:
            reasons.append(
                f"{window}: {entry['percent']}% has reached the {ceiling}% line"
            )
    return not reasons, reasons


def _limits(pairs: list[str]) -> dict[str, int]:
    limits: dict[str, int] = {}
    for pair in pairs:
        name, _, value = pair.partition("=")
        if name not in WINDOWS or not value.isdigit():
            raise ProbeError(f"--max expects <window>=<percent>, got {pair!r}")
        limits[name] = int(value)
    return limits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max", action="append", default=[], metavar="WINDOW=PCT",
                        help="gate: refuse when a window has reached this percent")
    parser.add_argument("--baseline", type=Path,
                        help="a previous snapshot; report the delta against it")
    parser.add_argument("--save", type=Path, help="write this snapshot to a file")
    args = parser.parse_args()

    try:
        limits = _limits(args.max)
        reading = snapshot()
    except ProbeError as error:
        # 3, not 1: an unreadable counter is not a budget breach, it is an
        # unknown. A caller must be able to tell those apart, and must not be
        # able to read this refusal as permission.
        print(json.dumps({"schema": SCHEMA, "error": str(error)}, sort_keys=True))
        return 3 if "unreachable" in str(error) or "returned" in str(error) else 2

    result: dict[str, Any] = {"reading": reading}
    if args.baseline:
        try:
            before = json.loads(args.baseline.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(json.dumps({"schema": SCHEMA, "error": f"baseline: {error}"}))
            return 2
        result["delta"] = delta(before, reading)
    if args.save:
        args.save.write_text(json.dumps(reading, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    passed, reasons = gate(reading, limits) if limits else (True, [])
    result["gate"] = {"limits": limits, "passed": passed, "reasons": reasons}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
