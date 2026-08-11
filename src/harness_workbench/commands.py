"""Authoritative public-command metadata.

The CLI and sensitivity campaign deliberately consume the same registry.
That makes a verdict-producing command visible to sensitivity at the moment
it is registered, before anybody remembers to write its known-red probe.

Keep this module dependency-free.  Command implementations live in ``cli``;
probe implementations live in ``sensitivity``.  Importing either here would
turn the registry into an import cycle instead of a shared boundary.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple


# name -> public metadata.  ``verdict_engine`` means that the command makes a
# pass/fail (or equivalent/refusal) claim about a run or campaign and therefore
# owes sensitivity a known-red probe.  Producers and inspectors are public
# commands too, but do not make such a verdict.
COMMANDS: Dict[str, Dict[str, Any]] = {
    "run": {"help": "execute a spec", "verdict_engine": False},
    "ls": {"help": "list runs", "verdict_engine": False},
    "show": {"help": "inspect one run", "verdict_engine": False},
    "verify": {"help": "check a run has not been edited", "verdict_engine": True},
    "diff": {"help": "compare two runs structurally", "verdict_engine": True},
    "sweep": {"help": "run a spec under many feature configurations", "verdict_engine": False},
    "interfere": {"help": "check a sweep for cross-feature interference", "verdict_engine": True},
    "blast": {"help": "inject faults into each feature, measure what survived", "verdict_engine": True},
    "catch": {"help": "perturb declared inputs, measure what a detector catches", "verdict_engine": True},
    "fidelity": {"help": "which questions can be answered from the record alone", "verdict_engine": True},
    "sensitivity": {"help": "account for every registered verdict engine with a deliberate violation", "verdict_engine": False},
    "efficacy": {"help": "invert each feature's decision, measure what noticed", "verdict_engine": True},
    "steady": {"help": "repeat an unchanged spec and reject a moving baseline", "verdict_engine": True},
    "effects": {"help": "compare bounded filesystem effects with an allowance", "verdict_engine": True},
    "interrupt": {"help": "terminate the runner at named lifecycle checkpoints", "verdict_engine": True},
    "order": {"help": "check a permutations sweep for order sensitivity", "verdict_engine": True},
    "confine": {"help": "audit declared record-power channels (not filesystem effects)", "verdict_engine": True},
    "replay": {"help": "re-execute a run from its own preserved spec and features", "verdict_engine": True},
    # Conformance is a public verdict engine reached through ``verify`` and
    # library consumers rather than a top-level command.  It remains in this
    # same registry so the complete public engine universe still has one
    # source.  ``cli_command`` makes that distinction explicit.
    "conform": {"help": "validate record invariants", "verdict_engine": True,
                "cli_command": False},
}


def metadata(name: str) -> Dict[str, Any]:
    """Return metadata for a registered public command/engine."""
    return COMMANDS[name]


def cli_commands() -> Tuple[str, ...]:
    """Names the CLI must register, in stable display order."""
    return tuple(name for name, meta in COMMANDS.items()
                 if meta.get("cli_command", True))


def public_verdict_engines() -> Tuple[str, ...]:
    """Derive sensitivity's universe from the command/engine registry."""
    return tuple(sorted(name for name, meta in COMMANDS.items()
                        if meta.get("verdict_engine")))
