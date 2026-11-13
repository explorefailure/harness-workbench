"""Parity checks for owner model-profile overlays."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from harness_workbench import model_profiles, reality


SUBJECTS = Path(__file__).resolve().parents[1] / "src/harness_workbench/subjects"
sys.path.insert(0, str(SUBJECTS))
try:
    import adapters
finally:
    sys.path.pop(0)


class ModelProfileOverlayTests(unittest.TestCase):
    def test_both_callers_apply_the_same_overlay_rules(self) -> None:
        safe_64 = "a" * 64
        unreadable = object()
        cases = (
            ("unset", None, None),
            ("empty", "", None),
            ("valid", {"profiles": {"mine": {"nested": {"value": 1}}}}, None),
            ("unreadable", unreadable, "HWB_MODEL_PROFILES is unreadable:"),
            ("invalid JSON", "{not json", "HWB_MODEL_PROFILES is unreadable:"),
            ("non-object top level", [], "HWB_MODEL_PROFILES must hold an object of profiles"),
            ("non-object profiles", {"profiles": []}, "HWB_MODEL_PROFILES must hold an object of profiles"),
            ("non-object profile", {"profiles": {"mine": []}}, "HWB_MODEL_PROFILES must hold an object of profiles"),
            ("clash", {"profiles": {"local-ollama": {}}}, "HWB_MODEL_PROFILES may not redefine committed profiles: local-ollama"),
            ("parent traversal", {"profiles": {"../../escape": {}}}, "HWB_MODEL_PROFILES profile name '../../escape' is unsafe; use letters, digits, dot, underscore or hyphen"),
            ("leading dot", {"profiles": {".hidden": {}}}, "HWB_MODEL_PROFILES profile name '.hidden' is unsafe; use letters, digits, dot, underscore or hyphen"),
            ("65 characters", {"profiles": {"a" * 65: {}}}, f"HWB_MODEL_PROFILES profile name {'a' * 65!r} is unsafe; use letters, digits, dot, underscore or hyphen"),
            ("64 characters", {"profiles": {safe_64: {}}}, None),
        )
        for label, payload, expected_message in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                environment: dict[str, str] = {}
                if payload == "":
                    environment[model_profiles.LOCAL_PROFILES_ENV] = ""
                elif payload is not None:
                    path = Path(directory) / "profiles.json"
                    environment[model_profiles.LOCAL_PROFILES_ENV] = str(path)
                    if payload is not unreadable:
                        path.write_text(
                            payload if isinstance(payload, str) else json.dumps(payload),
                            encoding="utf-8",
                        )
                outcomes = []
                for caller, exception in (
                    (reality._available_selection, reality.RealityError),
                    (adapters._model_selection, adapters.AdapterError),
                ):
                    with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
                        model_profiles.copy, "deepcopy", wraps=copy.deepcopy
                    ) as deepcopy:
                        try:
                            outcomes.append((caller(), None))
                        except exception as error:
                            outcomes.append((None, str(error)))
                    if label == "valid":
                        deepcopy.assert_called_once_with(payload["profiles"])
                self.assertEqual(outcomes[0], outcomes[1])
                if expected_message and expected_message.endswith(":"):
                    self.assertTrue(outcomes[0][1].startswith(expected_message))
                else:
                    self.assertEqual(expected_message, outcomes[0][1])
                if label == "valid":
                    self.assertIsNot(
                        payload["profiles"]["mine"],
                        outcomes[0][0]["profiles"]["mine"],
                    )

