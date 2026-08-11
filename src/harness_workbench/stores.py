"""Shared path boundary for run and campaign stores.

Campaign manifests and run evidence have different directory grammars.  If
their stores overlap, one kind of evidence can be mistaken for the other by
readers which deliberately expose incomplete directories.  Validate the
boundary before either writer creates anything.
"""
from __future__ import annotations

import os


class StoreOverlapError(ValueError):
    """A run store and campaign store do not have disjoint real paths."""


def require_disjoint(runs_root: str, campaign_root: str,
                     campaign_label: str) -> None:
    """Reject equal or nested stores after resolving current symlinks.

    ``commonpath`` can raise for paths on different Windows drives; those
    roots are necessarily disjoint.  ``realpath`` resolves existing symlink
    prefixes even when the final store directory has not been created yet.
    """
    real_runs = os.path.realpath(os.path.abspath(runs_root))
    real_campaign = os.path.realpath(os.path.abspath(campaign_root))
    try:
        common = os.path.commonpath((real_runs, real_campaign))
    except ValueError:
        return
    keys = tuple(os.path.normcase(path)
                 for path in (common, real_runs, real_campaign))
    if keys[0] in keys[1:]:
        raise StoreOverlapError(
            "run store and %s must not overlap (resolved to %s and %s)"
            % (campaign_label, real_runs, real_campaign))
