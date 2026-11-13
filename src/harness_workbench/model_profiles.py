"""Owner-side model profile overlays shared by Workbench callers."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
from typing import Any


LOCAL_PROFILES_ENV = "HWB_MODEL_PROFILES"
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

__all__ = ["LOCAL_PROFILES_ENV", "apply_overlay"]


def apply_overlay(
    selection: dict[str, Any], error: type[Exception]
) -> dict[str, Any]:
    """Add valid owner profiles to a committed model selection."""
    overlay_path = os.environ.get(LOCAL_PROFILES_ENV)
    if not overlay_path:
        return selection
    try:
        overlay = json.loads(
            Path(overlay_path).expanduser().read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as caught:
        raise error(f"{LOCAL_PROFILES_ENV} is unreadable: {caught}") from None
    profiles = overlay.get("profiles") if isinstance(overlay, dict) else None
    if not isinstance(profiles, dict) or not all(
        isinstance(name, str) and isinstance(profile, dict)
        for name, profile in profiles.items()
    ):
        raise error(f"{LOCAL_PROFILES_ENV} must hold an object of profiles")
    clashes = sorted(set(profiles) & set(selection["profiles"]))
    if clashes:
        raise error(
            f"{LOCAL_PROFILES_ENV} may not redefine committed profiles: "
            + ", ".join(clashes)
        )
    for name in profiles:
        if _PROFILE_NAME.fullmatch(name) is None:
            raise error(
                f"{LOCAL_PROFILES_ENV} profile name {name!r} is unsafe; use letters, "
                "digits, dot, underscore or hyphen"
            )
    selection["profiles"] = {
        **selection["profiles"],
        **copy.deepcopy(profiles),
    }
    return selection
