"""Oracle for extension mutation/guard ordering."""
from __future__ import annotations
import hashlib
from typing import Any

EXPECTED_EVENTS = {
    "mutate-first": [
        {"schema":"pi-hwb-composition/v0.1","handler":"mutator","before":"requested.txt","after":"redirected.txt"},
        {"schema":"pi-hwb-composition/v0.1","handler":"guard","observed":"redirected.txt","decision":"block"},
    ],
    "guard-first": [
        {"schema":"pi-hwb-composition/v0.1","handler":"guard","observed":"requested.txt","decision":"allow"},
        {"schema":"pi-hwb-composition/v0.1","handler":"mutator","before":"requested.txt","after":"redirected.txt"},
    ],
}

def evaluate(variant: str, capture: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    errors = list(capture.get("verdict", {}).get("errors", []))
    projection = capture.get("pi", {}).get("summary", {}).get("projection", {})
    executions = projection.get("tool_executions", [])
    if len(executions) != 2:
        errors.append("expected exactly two tool executions")
    else:
        treatment, control = executions
        if treatment.get("target_path") != "requested.txt" or treatment.get("arguments_stage") != "pre_tool_call_hook":
            errors.append("treatment proposal is not labelled as pre-hook evidence")
        if treatment.get("is_error") is not (variant == "mutate-first"):
            errors.append("treatment result does not match handler order")
        if control.get("target_path") != "permitted.txt" or control.get("is_error") is not False:
            errors.append("positive control failed")
    events = capture.get("evidence", {}).get("composition", {}).get("jsonl")
    if events != EXPECTED_EVENTS[variant]:
        errors.append("handler order/evidence does not match the variant")
    after = {item["path"]: item for item in capture.get("workspace", {}).get("after", [])}
    expected_paths = {"composition_task.md", "seed.txt", "permitted.txt"}
    if variant == "guard-first": expected_paths.add("redirected.txt")
    if set(after) != expected_paths:
        errors.append("durable effects do not match handler order")
    contents = {"permitted.txt": b"composition control\n"}
    if variant == "guard-first": contents["redirected.txt"] = b"composition treatment\n"
    for path, raw in contents.items():
        if after.get(path, {}).get("sha256") != hashlib.sha256(raw).hexdigest():
            errors.append(f"unexpected content for {path}")
    comparison = {"schema":"pi-hwb-composition-comparison/v0.1","variant":variant,
        "guard_observed": EXPECTED_EVENTS[variant][1 if variant == "mutate-first" else 0]["observed"],
        "treatment_effect": "redirected.txt" in after,
        "positive_control": "permitted.txt" in after,
        "projected_target_stage": "pre_tool_call_hook"}
    return errors, comparison
