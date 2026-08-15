import importlib.util
import base64
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
ARGUMENTS = {
    "path": "forbidden.txt",
    "content": "created by the Harness Workbench Pi control\n",
}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


normalizer = load_module("pi_hwb_normalizer", EXPERIMENT / "normalizer.py")
sys.modules["normalizer"] = normalizer
adapter = load_module("pi_hwb_adapter", EXPERIMENT / "adapter.py")
sys.modules["adapter"] = adapter
control_oracle = load_module("pi_hwb_control_oracle", EXPERIMENT / "control_oracle.py")
sys.modules["control_oracle"] = control_oracle
control_runner = load_module("pi_hwb_control_runner", EXPERIMENT / "control_runner.py")
coding_oracle = load_module("pi_hwb_coding_oracle", EXPERIMENT / "coding_oracle.py")
sys.modules["coding_oracle"] = coding_oracle
coding_runner = load_module("pi_hwb_coding_runner", EXPERIMENT / "coding_runner.py")
plan_oracle = load_module("pi_hwb_plan_oracle", EXPERIMENT / "plan_oracle.py")
sys.modules["plan_oracle"] = plan_oracle
plan_runner = load_module("pi_hwb_plan_runner", EXPERIMENT / "plan_runner.py")
composition_oracle = load_module("pi_hwb_composition_oracle", EXPERIMENT / "composition_oracle.py")
sys.modules["composition_oracle"] = composition_oracle
composition_runner = load_module("pi_hwb_composition_runner", EXPERIMENT / "composition_runner.py")
failure_order_oracle = load_module(
    "pi_hwb_failure_order_oracle", EXPERIMENT / "failure_order_oracle.py"
)
sys.modules["failure_order_oracle"] = failure_order_oracle
failure_order_runner = load_module(
    "pi_hwb_failure_order_runner", EXPERIMENT / "failure_order_runner.py"
)
policy_order_oracle = load_module(
    "pi_hwb_policy_order_oracle", EXPERIMENT / "policy_order_oracle.py"
)
sys.modules["policy_order_oracle"] = policy_order_oracle
policy_order_runner = load_module(
    "pi_hwb_policy_order_runner", EXPERIMENT / "policy_order_runner.py"
)
result_failure_oracle = load_module(
    "pi_hwb_result_failure_oracle", EXPERIMENT / "result_failure_oracle.py"
)
sys.modules["result_failure_oracle"] = result_failure_oracle
result_failure_runner = load_module(
    "pi_hwb_result_failure_runner", EXPERIMENT / "result_failure_runner.py"
)
result_rewrite_oracle = load_module(
    "pi_hwb_result_rewrite_oracle", EXPERIMENT / "result_rewrite_oracle.py"
)
sys.modules["result_rewrite_oracle"] = result_rewrite_oracle
result_rewrite_runner = load_module(
    "pi_hwb_result_rewrite_runner", EXPERIMENT / "result_rewrite_runner.py"
)
failure_rewrite_oracle = load_module(
    "pi_hwb_failure_rewrite_oracle", EXPERIMENT / "failure_rewrite_oracle.py"
)
sys.modules["failure_rewrite_oracle"] = failure_rewrite_oracle
failure_rewrite_runner = load_module(
    "pi_hwb_failure_rewrite_runner", EXPERIMENT / "failure_rewrite_runner.py"
)
pair_verifier = load_module("pi_hwb_pair", EXPERIMENT / "verify_pair.py")


def message_pair(message):
    return [
        {"type": "message_start", "message": message},
        {"type": "message_end", "message": message},
    ]


def first_cycle(*, is_error=False):
    user = {"role": "user", "content": "fixture", "timestamp": 1}
    assistant = {
        "role": "assistant",
        "content": [
            {
                "type": "toolCall",
                "id": "hwb-write-001",
                "name": "write",
                "arguments": ARGUMENTS,
            }
        ],
        "stopReason": "toolUse",
    }
    tool_result = {
        "role": "toolResult",
        "toolCallId": "hwb-write-001",
        "toolName": "write",
        "content": [],
        "isError": is_error,
    }
    return [
        {"type": "agent_start"},
        {"type": "turn_start"},
        *message_pair(user),
        {"type": "message_start", "message": assistant},
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "toolcall_start", "contentIndex": 0},
        },
        {"type": "message_end", "message": assistant},
        {
            "type": "tool_execution_start",
            "toolCallId": "hwb-write-001",
            "toolName": "write",
            "args": ARGUMENTS,
        },
        {
            "type": "tool_execution_end",
            "toolCallId": "hwb-write-001",
            "toolName": "write",
            "result": {"content": []},
            "isError": is_error,
        },
        *message_pair(tool_result),
        {"type": "turn_end", "message": assistant, "toolResults": [tool_result]},
        {"type": "turn_start"},
        *message_pair(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "complete"}],
                "stopReason": "stop",
            }
        ),
        {"type": "turn_end", "message": {}, "toolResults": []},
        {"type": "agent_end", "messages": []},
    ]


def valid_events(*, is_error=False):
    return [
        {"type": "session", "version": 3, "id": "volatile", "cwd": "/tmp/x"},
        *first_cycle(is_error=is_error),
        {"type": "agent_settled"},
    ]


def as_jsonl(events):
    return "\n".join(json.dumps(event) for event in events) + "\n"


def valid_coding_capture():
    before, after = coding_oracle.expected_manifests()
    executions = coding_oracle.expected_tool_evidence()
    assistant_calls = [
        {
            key: value
            for key, value in item.items()
            if key not in {"is_error", "result_sha256"}
        }
        for item in executions
    ]
    return {
        "verdict": {"passed": True, "errors": []},
        "workspace": {"before": before, "after": after},
        "pi": {
            "summary": {
                "valid": True,
                "errors": [],
                "projection": {
                    "assistant_stop_reasons": [
                        "toolUse",
                        "toolUse",
                        "toolUse",
                        "toolUse",
                        "toolUse",
                        "stop",
                    ],
                    "assistant_tool_calls": assistant_calls,
                    "tool_executions": executions,
                    "event_types": {"agent_start": 1, "agent_settled": 1},
                },
            }
        },
    }


class PiNormalizerTests(unittest.TestCase):
    def test_normalizes_ordered_lifecycle_and_correlated_tool(self):
        summary = normalizer.normalize_jsonl(as_jsonl(valid_events(is_error=True)))
        self.assertTrue(summary["valid"], summary["errors"])
        execution = summary["projection"]["tool_executions"][0]
        self.assertEqual("hwb-write-001", execution["tool_call_id"])
        self.assertEqual("forbidden.txt", execution["target_path"])
        self.assertEqual("pre_tool_call_hook", execution["arguments_stage"])
        self.assertEqual("post_tool_result_hook", execution["result_stage"])
        self.assertTrue(execution["is_error"])

    def test_rejects_non_json_noise(self):
        with self.assertRaisesRegex(normalizer.StreamError, "invalid JSON at line 2"):
            normalizer.normalize_jsonl('{"type":"session"}\nnot-json\n')

    def test_rejects_out_of_order_lifecycle(self):
        events = [
            {"type": "session", "version": 3},
            {"type": "agent_end", "messages": []},
            {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop"}},
            {"type": "agent_start"},
            {"type": "agent_settled"},
        ]
        summary = normalizer.summarize(events)
        self.assertFalse(summary["valid"])
        self.assertTrue(
            any("agent_end has no active agent" in error for error in summary["errors"])
        )
        self.assertTrue(
            any("message_end has no matching" in error for error in summary["errors"])
        )

    def test_rejects_duplicate_normal_terminal(self):
        events = valid_events()
        insertion = len(events) - 2
        extra = [
            {"type": "turn_start"},
            *message_pair(
                {"role": "assistant", "content": [], "stopReason": "stop"}
            ),
            {"type": "turn_end", "message": {}, "toolResults": []},
        ]
        events[insertion:insertion] = extra
        summary = normalizer.summarize(events)
        self.assertFalse(summary["valid"])
        self.assertIn(
            "expected exactly one normally stopped assistant message, saw 2",
            summary["errors"],
        )

    def test_missing_settled_is_invalid(self):
        summary = normalizer.summarize(valid_events()[:-1])
        self.assertFalse(summary["valid"])
        self.assertIn("expected exactly one agent_settled, saw 0", summary["errors"])

    def test_truncated_tool_stream_is_invalid(self):
        events = valid_events()
        events = [event for event in events if event["type"] != "tool_execution_end"]
        summary = normalizer.summarize(events)
        self.assertFalse(summary["valid"])
        self.assertIn(
            "tool_execution_start has no matching end: hwb-write-001",
            summary["errors"],
        )

    def test_extension_error_is_invalid(self):
        events = valid_events()
        events.insert(-1, {"type": "extension_error", "error": "fixture failure"})
        summary = normalizer.summarize(events)
        self.assertFalse(summary["valid"])
        self.assertTrue(any("extension_error" in error for error in summary["errors"]))

    def test_unknown_events_are_retained_additively(self):
        events = valid_events()
        events.insert(-1, {"type": "future_event", "value": 1})
        summary = normalizer.summarize(events)
        self.assertTrue(summary["valid"], summary["errors"])
        self.assertEqual(["future_event"], summary["projection"]["unknown_event_types"])

    def test_rejects_pinned_session_protocol_version_drift(self):
        events = valid_events()
        events[0]["version"] = 4
        summary = normalizer.summarize(events)
        self.assertFalse(summary["valid"])
        self.assertIn("Pi session version is 4; expected 3", summary["errors"])

    def test_tool_argument_mismatch_is_invalid(self):
        events = valid_events()
        start = next(event for event in events if event["type"] == "tool_execution_start")
        start["args"] = {**ARGUMENTS, "path": "other.txt"}
        summary = normalizer.summarize(events)
        self.assertFalse(summary["valid"])
        self.assertTrue(any("disagrees with assistant" in error for error in summary["errors"]))

    def test_balanced_retry_can_justify_second_agent_cycle(self):
        failed = {
            "role": "assistant",
            "content": [],
            "stopReason": "error",
        }
        events = [
            {"type": "session", "version": 3},
            {"type": "agent_start"},
            {"type": "turn_start"},
            *message_pair(failed),
            {"type": "turn_end", "message": failed, "toolResults": []},
            {"type": "agent_end", "messages": []},
            {"type": "auto_retry_start"},
            {"type": "auto_retry_end"},
            *first_cycle(),
            {"type": "agent_settled"},
        ]
        summary = normalizer.summarize(events)
        self.assertTrue(summary["valid"], summary["errors"])
        self.assertEqual(2, summary["projection"]["event_types"]["agent_start"])

    def test_balanced_compaction_can_justify_second_agent_cycle(self):
        events = [
            {"type": "session", "version": 3},
            {"type": "agent_start"},
            {"type": "agent_end", "messages": []},
            {"type": "compaction_start"},
            {"type": "compaction_end"},
            *first_cycle(),
            {"type": "agent_settled"},
        ]
        summary = normalizer.summarize(events)
        self.assertTrue(summary["valid"], summary["errors"])

    def test_lifecycle_deletion_matrix_rejects_truncated_streams(self):
        required_types = (
            "agent_start",
            "agent_end",
            "turn_start",
            "turn_end",
            "message_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "agent_settled",
        )
        for event_type in required_types:
            with self.subTest(event_type=event_type):
                events = valid_events()
                index = next(
                    index
                    for index, event in enumerate(events)
                    if event["type"] == event_type
                )
                del events[index]
                summary = normalizer.summarize(events)
                self.assertFalse(summary["valid"], summary)

    def test_lifecycle_duplication_matrix_rejects_duplicate_framing(self):
        for event_type in (
            "session",
            "agent_start",
            "agent_end",
            "turn_start",
            "turn_end",
            "message_start",
            "message_end",
            "tool_execution_start",
            "tool_execution_end",
            "agent_settled",
        ):
            with self.subTest(event_type=event_type):
                events = valid_events()
                index = next(
                    index
                    for index, event in enumerate(events)
                    if event["type"] == event_type
                )
                events.insert(index, copy.deepcopy(events[index]))
                summary = normalizer.summarize(events)
                self.assertFalse(summary["valid"], summary)


class PiCodingOracleTests(unittest.TestCase):
    def test_accepts_only_the_complete_red_repair_green_path(self):
        errors, comparison = coding_oracle.evaluate(valid_coding_capture())
        self.assertEqual([], errors)
        self.assertTrue(comparison["first_test_failed"])
        self.assertTrue(comparison["final_test_passed"])
        self.assertEqual(["slugger.py"], comparison["changed_paths"])
        self.assertTrue(comparison["invariants_unchanged"])

    def test_rejects_false_success_matrix(self):
        cases = (
            "first-test-passed",
            "missing-initial-test",
            "test-command-missing",
            "test-process-signaled",
            "wrong-edit-target",
            "final-test-failed",
            "missing-final-test",
            "volatile-final-test-output",
            "implementation-unchanged",
            "invariant-changed",
            "unexpected-file",
            "tool-order-changed",
            "adapter-failed",
        )
        for case in cases:
            with self.subTest(case=case):
                capture = valid_coding_capture()
                projection = capture["pi"]["summary"]["projection"]
                executions = projection["tool_executions"]
                calls = projection["assistant_tool_calls"]
                after = capture["workspace"]["after"]

                if case == "first-test-passed":
                    executions[2]["is_error"] = False
                elif case == "missing-initial-test":
                    del executions[2]
                    del calls[2]
                elif case == "test-command-missing":
                    changed = normalizer.canonical_digest(
                        {"command": "missing-hwb-test-command"}
                    )
                    executions[2]["arguments_sha256"] = changed
                    calls[2]["arguments_sha256"] = changed
                elif case == "test-process-signaled":
                    executions[2]["result_sha256"] = "sha256:" + "0" * 64
                elif case == "wrong-edit-target":
                    executions[3]["target_path"] = "other.py"
                    calls[3]["target_path"] = "other.py"
                elif case == "final-test-failed":
                    executions[4]["is_error"] = True
                elif case == "missing-final-test":
                    del executions[4]
                    del calls[4]
                elif case == "volatile-final-test-output":
                    executions[4]["result_sha256"] = "sha256:" + "2" * 64
                elif case == "implementation-unchanged":
                    before_slugger = next(
                        item
                        for item in capture["workspace"]["before"]
                        if item["path"] == "slugger.py"
                    )
                    after_slugger = next(
                        item for item in after if item["path"] == "slugger.py"
                    )
                    after_slugger.update(before_slugger)
                elif case == "invariant-changed":
                    invariant = next(
                        item for item in after if item["path"] == "test_slugger.py"
                    )
                    invariant["sha256"] = "0" * 64
                elif case == "unexpected-file":
                    after.append(
                        {
                            "path": "surprise.txt",
                            "mode": 0o644,
                            "size": 1,
                            "sha256": "1" * 64,
                        }
                    )
                elif case == "tool-order-changed":
                    executions[3], executions[4] = executions[4], executions[3]
                    calls[3], calls[4] = calls[4], calls[3]
                elif case == "adapter-failed":
                    capture["verdict"] = {
                        "passed": False,
                        "errors": ["Pi exited with status 7"],
                    }

                errors, _comparison = coding_oracle.evaluate(capture)
                self.assertTrue(errors, f"false success was accepted: {case}")

class PiExperimentSurfaceTests(unittest.TestCase):
    def test_specs_bind_every_executable_input(self):
        expected = set(control_runner.EXPERIMENT_INPUTS)
        for name in ("block.json", "allow.json"):
            raw = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
            self.assertEqual("confirmation", raw["run_class"])
            self.assertEqual("harness_workbench:builtin", raw["features_root"])
            self.assertEqual(["freeze", "receipt"], [item["name"] for item in raw["features"]])
            self.assertEqual(expected, set(raw["steps"][0]["inputs"]))

        coding = json.loads((EXPERIMENT / "coding.json").read_text(encoding="utf-8"))
        self.assertEqual("confirmation", coding["run_class"])
        self.assertEqual("harness_workbench:builtin", coding["features_root"])
        self.assertEqual(
            ["freeze", "receipt"],
            [item["name"] for item in coding["features"]],
        )
        self.assertEqual(
            {
                "run_coding_adapter.sh",
                "coding_adapter_config.json",
                "adapter.py",
                "coding_runner.py",
                "coding_oracle.py",
                "normalizer.py",
                "coding_provider.ts",
                "pin.json",
                "coding_task.md",
                "coding_fixture/slugger.py",
                "coding_fixture/test_slugger.py",
            },
            set(coding["steps"][0]["inputs"]),
        )
        self.assertEqual(set(coding_runner.CODING_INPUTS), set(coding["steps"][0]["inputs"]))

        for name, variant in (("plan_mode.json", "plan"), ("action_mode.json", "act")):
            raw = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
            self.assertEqual("confirmation", raw["run_class"])
            self.assertEqual(
                ["./run_plan_mode.sh", variant], raw["steps"][0]["argv"]
            )
            self.assertEqual(
                set(plan_runner.PLAN_INPUTS), set(raw["steps"][0]["inputs"])
            )

        for name, variant in (("mutate_first.json", "mutate-first"), ("guard_first.json", "guard-first")):
            raw = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
            self.assertEqual(["./run_composition.sh", variant], raw["steps"][0]["argv"])
            self.assertEqual(
                set(composition_runner.INPUTS)
                | set(json.loads((EXPERIMENT / "composition_adapter_config.json").read_text())["inputs"]),
                set(raw["steps"][0]["inputs"]),
            )

        for name, variant in (
            ("throw_first.json", "throw-first"),
            ("audit_first.json", "audit-first"),
        ):
            raw = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
            self.assertEqual(
                ["./run_failure_order.sh", variant], raw["steps"][0]["argv"]
            )
            self.assertEqual(
                set(failure_order_runner.INPUTS)
                | set(
                    json.loads(
                        (EXPERIMENT / "composition_adapter_config.json").read_text()
                    )["inputs"]
                ),
                set(raw["steps"][0]["inputs"]),
            )

        for name, variant in (
            ("block_first.json", "block-first"),
            ("allow_first.json", "allow-first"),
        ):
            raw = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
            self.assertEqual(
                ["./run_policy_order.sh", variant], raw["steps"][0]["argv"]
            )
            self.assertEqual(
                set(policy_order_runner.INPUTS)
                | set(
                    json.loads(
                        (EXPERIMENT / "composition_adapter_config.json").read_text()
                    )["inputs"]
                ),
                set(raw["steps"][0]["inputs"]),
            )

        for name, variant in (
            ("result_throw_first.json", "throw-first"),
            ("result_audit_first.json", "audit-first"),
        ):
            raw = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
            self.assertEqual(
                ["./run_result_failure.sh", variant], raw["steps"][0]["argv"]
            )
            self.assertEqual(
                set(result_failure_runner.INPUTS)
                | set(
                    json.loads(
                        (EXPERIMENT / "composition_adapter_config.json").read_text()
                    )["inputs"]
                ),
                set(raw["steps"][0]["inputs"]),
            )

        for name, variant in (
            ("result_mask_first.json", "mask-first"),
            ("result_restore_first.json", "restore-first"),
        ):
            raw = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
            self.assertEqual(
                ["./run_result_rewrite.sh", variant], raw["steps"][0]["argv"]
            )
            self.assertEqual(
                set(result_rewrite_runner.INPUTS)
                | set(
                    json.loads(
                        (EXPERIMENT / "composition_adapter_config.json").read_text()
                    )["inputs"]
                ),
                set(raw["steps"][0]["inputs"]),
            )

        for name, variant in (
            ("failure_honest.json", "honest"),
            ("failure_falsified.json", "falsified"),
        ):
            raw = json.loads((EXPERIMENT / name).read_text(encoding="utf-8"))
            self.assertEqual(
                ["./run_failure_rewrite.sh", variant], raw["steps"][0]["argv"]
            )
            self.assertEqual(
                set(failure_rewrite_runner.INPUTS)
                | set(
                    json.loads(
                        (EXPERIMENT / "failure_rewrite_adapter_config.json").read_text()
                    )["inputs"]
                ),
                set(raw["steps"][0]["inputs"]),
            )

    def test_pin_is_exact(self):
        pin = json.loads((EXPERIMENT / "pin.json").read_text(encoding="utf-8"))
        self.assertEqual("0.84.1", pin["version"])
        self.assertEqual("22.22.3", pin["node_version"])
        self.assertRegex(pin["source_commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(pin["npm_integrity"].startswith("sha512-"))
        for key in (
            "package_json_sha256",
            "npm_shrinkwrap_sha256",
            "launcher_sha256",
            "package_tree_sha256",
        ):
            self.assertRegex(pin[key], r"^[0-9a-f]{64}$")

    def test_install_identity_drift_matrix_rejects_changed_bytes(self):
        base_files = {
            "package.json": json.dumps(
                {"name": "example-pi", "version": "1.2.3"}
            ).encode("utf-8"),
            "npm-shrinkwrap.json": b'{"lockfileVersion":3}\n',
            "dist/cli.js": b"console.log('fixture');\n",
            "dist/other.js": b"export const fixture = true;\n",
        }

        def prepare(root):
            for relative, content in base_files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            launcher = root / "dist" / "cli.js"
            _count, tree_digest = adapter.package_tree_digest(root)
            pin = {
                "npm_package": "example-pi",
                "version": "1.2.3",
                "package_json_sha256": adapter.sha256_file(root / "package.json"),
                "npm_shrinkwrap_sha256": adapter.sha256_file(
                    root / "npm-shrinkwrap.json"
                ),
                "launcher_sha256": adapter.sha256_file(launcher),
                "package_tree_sha256": tree_digest,
            }
            return launcher, pin

        with tempfile.TemporaryDirectory() as directory:
            clean_root = Path(directory) / "clean"
            launcher, pin = prepare(clean_root)
            identity = adapter.verify_pi_install(launcher, pin)
            self.assertEqual("example-pi", identity["package_name"])

        mutations = (
            ("package-version", "package.json", b'{"name":"example-pi","version":"9.9.9"}'),
            ("package-metadata", "package.json", b'{"name":"example-pi","version":"1.2.3","changed":true}'),
            ("shrinkwrap", "npm-shrinkwrap.json", b'{"lockfileVersion":2}\n'),
            ("launcher", "dist/cli.js", b"console.log('mutated');\n"),
            ("owned-tree", "dist/other.js", b"export const fixture = false;\n"),
        )
        for name, relative, changed in mutations:
            with self.subTest(identity_mutation=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "package"
                launcher, pin = prepare(root)
                (root / relative).write_bytes(changed)
                with self.assertRaises(adapter.AdapterError):
                    adapter.verify_pi_install(launcher, pin)

    def test_fixture_has_no_control_output_files(self):
        self.assertFalse((EXPERIMENT / "fixture" / "forbidden.txt").exists())
        self.assertFalse((EXPERIMENT / "fixture" / "permitted.txt").exists())

    def test_generic_adapter_contains_no_control_oracle(self):
        source = (EXPERIMENT / "adapter.py").read_text(encoding="utf-8")
        config = json.loads(
            (EXPERIMENT / "adapter_config.json").read_text(encoding="utf-8")
        )
        for control_word in ("forbidden.txt", "permitted.txt", "PI_HWB_GUARD_MODE"):
            self.assertNotIn(control_word, source)
        self.assertNotIn("guard_extension.ts", config["extensions"])
        self.assertNotIn("control_oracle.py", config["inputs"])

    def test_config_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = json.loads(
                (EXPERIMENT / "adapter_config.json").read_text(encoding="utf-8")
            )
            config["experiment_oracle"] = "forbidden-write"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(adapter.AdapterError, "unknown adapter config"):
                adapter.load_config(path)

    def test_config_reserves_adapter_owned_pi_framing(self):
        source = json.loads(
            (EXPERIMENT / "adapter_config.json").read_text(encoding="utf-8")
        )
        cases = (
            (["--mode", "rpc"], "exactly one '--mode json'"),
            ([*source["pi_arguments"], "--print"], "dedicated field"),
            ([*source["pi_arguments"], "-e"], "dedicated field"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for arguments, expected in cases:
                with self.subTest(arguments=arguments[-2:]):
                    config = {**source, "pi_arguments": arguments}
                    path.write_text(json.dumps(config), encoding="utf-8")
                    with self.assertRaisesRegex(adapter.AdapterError, expected):
                        adapter.load_config(path)

    def test_config_requires_closed_positive_capture_limits(self):
        source = json.loads(
            (EXPERIMENT / "adapter_config.json").read_text(encoding="utf-8")
        )
        cases = (
            (None, "must contain exactly"),
            ({"stdout_bytes": 1, "stderr_bytes": 1}, "must contain exactly"),
            (
                {
                    "stdout_bytes": 1,
                    "stderr_bytes": 1,
                    "evidence_bytes": 1,
                    "extra": 1,
                },
                "must contain exactly",
            ),
            (
                {"stdout_bytes": 0, "stderr_bytes": 1, "evidence_bytes": 1},
                "positive integer",
            ),
            (
                {"stdout_bytes": True, "stderr_bytes": 1, "evidence_bytes": 1},
                "positive integer",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for limits, expected in cases:
                with self.subTest(limits=limits):
                    config = copy.deepcopy(source)
                    if limits is None:
                        del config["capture_limits"]
                    else:
                        config["capture_limits"] = limits
                    path.write_text(json.dumps(config), encoding="utf-8")
                    with self.assertRaisesRegex(adapter.AdapterError, expected):
                        adapter.load_config(path)

    def test_path_provenance_rejects_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "regular.txt"
            regular.write_text("bound", encoding="utf-8")
            link = root / "link.txt"
            link.symlink_to(regular)
            with self.assertRaisesRegex(adapter.AdapterError, "stay below"):
                adapter._regular_relative(root, "../regular.txt", "test")
            with self.assertRaisesRegex(adapter.AdapterError, "not a regular file"):
                adapter._regular_relative(root, "link.txt", "test")
            with self.assertRaisesRegex(adapter.AdapterError, "stay below"):
                adapter._evidence_path(root, "nested/../../escape")

    def test_input_provenance_rejects_duplicates_and_fixture_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "regular.txt"
            regular.write_text("bound", encoding="utf-8")
            with self.assertRaisesRegex(adapter.AdapterError, "duplicate adapter input"):
                adapter._unique_relative_inputs(
                    root, ["regular.txt", "regular.txt"]
                )

            fixture = root / "fixture"
            fixture.mkdir()
            (fixture / "seed.txt").write_text("seed", encoding="utf-8")
            (fixture / "linked.txt").symlink_to(regular)
            with self.assertRaisesRegex(adapter.AdapterError, "symbolic links"):
                adapter._fixture_files(root, fixture)

    def test_capture_rejects_an_omitted_consumed_input_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "experiment"
            shutil.copytree(EXPERIMENT, copied)
            config_path = copied / "text_adapter_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["inputs"].remove("text_task.md")
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(
                adapter.AdapterError, "omitted consumed input.*text_task.md"
            ):
                adapter.capture(config_path, pi_name="not-needed")

    def test_minimal_environment_drops_host_credentials_and_pi_state(self):
        sensitive = {
            "ANTHROPIC_API_KEY": "must-not-cross",
            "OPENAI_API_KEY": "must-not-cross",
            "PI_CODING_AGENT_SESSION_DIR": "/host/sessions",
            "PI_PACKAGE_DIR": "/host/packages",
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, sensitive, clear=False
        ):
            env = adapter.minimal_environment(Path(directory), {})
        for name in sensitive:
            self.assertNotIn(name, env)
        self.assertEqual("1", env["PI_OFFLINE"])
        self.assertNotEqual(os.environ.get("HOME"), env["HOME"])

    def test_file_manifest_preserves_unicode_nested_path_mode_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "nested dir" / "naïve file.txt"
            path.parent.mkdir()
            raw = "héllo\r\n".encode("utf-8")
            path.write_bytes(raw)
            path.chmod(0o640)
            manifest = adapter.file_manifest(root)
        self.assertEqual(
            [
                {
                    "path": "nested dir/naïve file.txt",
                    "size": len(raw),
                    "mode": 0o640,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            ],
            manifest,
        )

    def test_raw_evidence_boundary_and_digest_pressure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.bin"
            raw = b"abcdefgh"
            path.write_bytes(raw)
            exact = adapter.capture_evidence_file(
                path, required=True, format_name="binary", max_bytes=len(raw)
            )
            self.assertEqual(base64.b64encode(raw).decode("ascii"), exact["raw_base64"])
            self.assertEqual(len(raw), exact["size"])
            self.assertEqual([], exact["errors"])

            oversized = adapter.capture_evidence_file(
                path, required=True, format_name="binary", max_bytes=len(raw) - 1
            )
            self.assertIsNone(oversized["raw_base64"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), oversized["raw_sha256"])
            self.assertRegex(oversized["errors"][0], "exceeds 7-byte")

    def test_raw_evidence_framing_and_encoding_matrix(self):
        cases = (
            ("crlf", b'{"value":1}\r\n{"value":2}\r\n', "jsonl", 0, 2),
            ("blank", b'{"value":1}\n\n', "jsonl", 1, 1),
            ("partial", b'{"value":', "jsonl", 1, 0),
            ("invalid-utf8", b"\xff\xfe", "utf8", 1, None),
            ("binary", b"\xff\xfe", "binary", 0, None),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.bin"
            for name, raw, format_name, error_count, record_count in cases:
                with self.subTest(name=name):
                    path.write_bytes(raw)
                    item = adapter.capture_evidence_file(
                        path,
                        required=True,
                        format_name=format_name,
                        max_bytes=1024,
                    )
                    self.assertEqual(error_count, len(item["errors"]), item)
                    if record_count is not None:
                        self.assertEqual(record_count, len(item["jsonl"] or []))
                    self.assertEqual(raw, base64.b64decode(item["raw_base64"]))
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), item["raw_sha256"])

    def test_raw_evidence_rejects_non_regular_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"secret")
            link = root / "link"
            link.symlink_to(target)
            item = adapter.capture_evidence_file(
                link, required=True, format_name="binary"
            )
            self.assertRegex(item["errors"][0], "not a regular file")
            self.assertEqual(base64.b64encode(b"").decode("ascii"), item["raw_base64"])
            self.assertEqual(hashlib.sha256(b"").hexdigest(), item["raw_sha256"])

    def test_pi_extension_stderr_is_projected_without_host_paths(self):
        base = Path("/private/example/experiment")
        extension = base / "fixture_extension.ts"
        records = adapter.parse_extension_errors(
            "ordinary warning\n"
            "Extension error (/private/example/experiment/fixture_extension.ts): "
            "deterministic failure\n",
            [extension],
            base,
        )
        self.assertEqual(
            [
                {
                    "schema": "pi-hwb-extension-error/v0.1",
                    "extension": "fixture_extension.ts",
                    "error": "deterministic failure",
                }
            ],
            records,
        )
        self.assertNotIn("/private/example", json.dumps(records))

    @unittest.skipUnless(os.name == "posix", "capture-limit contract is POSIX-only")
    def test_output_limit_stops_and_bounds_the_owned_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = adapter.run_bounded(
                [
                    sys.executable,
                    "-c",
                    "import os,time; os.write(1, b'x' * 65536); time.sleep(30)",
                ],
                cwd=root,
                env=os.environ.copy(),
                timeout=5.0,
                evidence_root=root,
                stdout_limit=1024,
                stderr_limit=1024,
            )
        self.assertIn("stdout", result["output_limit_exceeded"])
        self.assertGreater(result["stdout_size"], 1024)
        self.assertIsNone(result["stdout"])
        self.assertFalse(result["post_cleanup_group_alive"])

    @unittest.skipUnless(os.name == "posix", "process contract is POSIX-only")
    def test_nonzero_child_exit_is_captured_without_hanging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = adapter.run_bounded(
                [sys.executable, "-c", "raise SystemExit(7)"],
                cwd=root,
                env=os.environ.copy(),
                timeout=2.0,
                evidence_root=root,
            )
        self.assertEqual(7, result["returncode"])
        self.assertFalse(result["timed_out"])
        self.assertFalse(result["post_cleanup_group_alive"])

    @unittest.skipUnless(os.name == "posix", "permission contract is POSIX-only")
    def test_unwritable_workspace_parent_fails_before_pi_launch(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        with tempfile.TemporaryDirectory() as directory:
            locked = Path(directory) / "locked"
            locked.mkdir()
            locked.chmod(0)
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(EXPERIMENT / "adapter.py"),
                        str(EXPERIMENT / "text_adapter_config.json"),
                        "--workspace-parent",
                        str(locked),
                    ],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
            finally:
                locked.chmod(0o700)
        envelope = json.loads(result.stdout)
        self.assertEqual(2, result.returncode)
        self.assertEqual("pi-hwb-adapter-run/v0.1", envelope["schema"])
        self.assertIn("Permission denied", envelope["error"])

    def test_missing_guard_decision_fails_explicitly(self):
        capture = {
            "verdict": {
                "errors": [
                    "evidence guard_decisions: required evidence file was not created"
                ]
            },
            "evidence": {"guard_decisions": {"jsonl": []}},
            "workspace": {"after": []},
            "pi": {"summary": None},
        }
        errors, _comparison = control_oracle.evaluate("block", capture)
        self.assertTrue(any("required evidence file" in error for error in errors))
        self.assertIn("expected one guard decision, saw 0", errors)

    def test_malformed_guard_decision_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.jsonl"
            path.write_bytes(b"not-json\n")
            evidence = adapter.capture_evidence_file(
                path, required=True, format_name="jsonl"
            )
        self.assertEqual([], evidence["jsonl"])
        self.assertEqual(b"not-json\n", base64.b64decode(evidence["raw_base64"]))
        self.assertRegex(evidence["errors"][0], "invalid JSONL at line 1")

    @unittest.skipUnless(os.name == "posix", "process-group contract is POSIX-only")
    def test_timeout_kills_the_owned_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = adapter.run_bounded(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys,time; "
                    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                    "time.sleep(30)",
                ],
                cwd=root,
                env=os.environ.copy(),
                timeout=0.2,
                evidence_root=root,
            )
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["post_cleanup_group_alive"])

    @unittest.skipUnless(os.name == "posix", "detached-session contract is POSIX-only")
    def test_detached_descriptor_holder_does_not_hang_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "escaped.pid"
            script = (
                "import pathlib,subprocess,sys; "
                "p=subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(30)'], start_new_session=True); "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid))"
            )
            started = time.monotonic()
            result = adapter.run_bounded(
                [sys.executable, "-c", script],
                cwd=root,
                env=os.environ.copy(),
                timeout=2.0,
                evidence_root=root,
            )
            elapsed = time.monotonic() - started
            escaped_pid = int(pid_path.read_text())
            try:
                os.kill(escaped_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.assertLess(elapsed, 1.5)
        self.assertEqual(0, result["returncode"])
        self.assertFalse(result["post_cleanup_group_alive"])

    def test_pinned_pi_runs_both_controls_offline_when_installed(self):
        pi = shutil.which("pi")
        if pi is None:
            self.skipTest("Pi is not installed")
        version = subprocess.run(
            [pi, "--version"], check=True, stdout=subprocess.PIPE, text=True
        ).stdout.strip()
        if version != "0.84.1":
            self.skipTest(f"Pi 0.84.1 is required, found {version}")

        with tempfile.TemporaryDirectory() as parent:
            generic = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENT / "adapter.py"),
                    str(EXPERIMENT / "adapter_config.json"),
                    "--workspace-parent",
                    parent,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            generic_envelope = json.loads(generic.stdout)
            self.assertEqual("pi-hwb-adapter-run/v0.1", generic_envelope["schema"])
            self.assertTrue(generic_envelope["verdict"]["passed"])
            self.assertNotIn("comparison", generic_envelope)

            envelopes = {}
            for variant in ("block", "allow"):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(EXPERIMENT / "control_runner.py"),
                        "--variant",
                        variant,
                        "--workspace-parent",
                        parent,
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                envelopes[variant] = json.loads(result.stdout)

            self.assertTrue(envelopes["block"]["verdict"]["passed"])
            self.assertTrue(envelopes["allow"]["verdict"]["passed"])
            self.assertFalse(envelopes["block"]["comparison"]["forbidden_file_exists"])
            self.assertTrue(envelopes["allow"]["comparison"]["forbidden_file_exists"])
            self.assertTrue(envelopes["block"]["comparison"]["permitted_file_exists"])
            self.assertTrue(envelopes["allow"]["comparison"]["permitted_file_exists"])
            self.assertEqual(
                envelopes["block"]["adapter"]["configuration"]["input_digests"],
                envelopes["allow"]["adapter"]["configuration"]["input_digests"],
            )
            self.assertFalse(
                envelopes["block"]["adapter"]["pi"]["post_cleanup_group_alive"]
            )
            self.assertEqual(
                envelopes["block"]["adapter"]["pi"]["stdout_sha256"],
                hashlib.sha256(
                    base64.b64decode(
                        envelopes["block"]["adapter"]["pi"]["stdout_base64"]
                    )
                ).hexdigest(),
            )

    def test_generic_adapter_runs_independent_text_workload_when_pi_is_installed(self):
        pi = shutil.which("pi")
        if pi is None:
            self.skipTest("Pi is not installed")
        version = subprocess.run(
            [pi, "--version"], check=True, stdout=subprocess.PIPE, text=True
        ).stdout.strip()
        if version != "0.84.1":
            self.skipTest(f"Pi 0.84.1 is required, found {version}")

        with tempfile.TemporaryDirectory() as parent:
            result = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENT / "adapter.py"),
                    str(EXPERIMENT / "text_adapter_config.json"),
                    "--workspace-parent",
                    parent,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        envelope = json.loads(result.stdout)
        self.assertTrue(envelope["verdict"]["passed"], envelope["verdict"]["errors"])
        self.assertEqual({}, envelope["evidence"])
        self.assertEqual(
            ["text_provider.ts"], envelope["configuration"]["extension_inputs"]
        )
        self.assertEqual(
            {
                "adapter.py",
                "normalizer.py",
                "pin.json",
                "text_adapter_config.json",
                "text_fixture/marker.txt",
                "text_provider.ts",
                "text_task.md",
            },
            set(envelope["configuration"]["input_digests"]),
        )
        self.assertEqual(
            [],
            envelope["pi"]["summary"]["projection"]["assistant_tool_calls"],
        )
        self.assertEqual(
            [], envelope["pi"]["summary"]["projection"]["tool_executions"]
        )
        self.assertEqual(
            envelope["workspace"]["before"], envelope["workspace"]["after"]
        )
        self.assertIn(
            "Independent text-only Pi workload completed.",
            envelope["pi"]["stdout_jsonl"],
        )

    def test_generic_adapter_runs_read_edit_workload_when_pi_is_installed(self):
        pi = shutil.which("pi")
        if pi is None:
            self.skipTest("Pi is not installed")
        version = subprocess.run(
            [pi, "--version"], check=True, stdout=subprocess.PIPE, text=True
        ).stdout.strip()
        if version != "0.84.1":
            self.skipTest(f"Pi 0.84.1 is required, found {version}")

        with tempfile.TemporaryDirectory() as parent:
            result = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENT / "adapter.py"),
                    str(EXPERIMENT / "read_edit_adapter_config.json"),
                    "--workspace-parent",
                    parent,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        envelope = json.loads(result.stdout)
        projection = envelope["pi"]["summary"]["projection"]
        self.assertTrue(envelope["verdict"]["passed"], envelope["verdict"]["errors"])
        self.assertEqual(["toolUse", "toolUse", "stop"], projection["assistant_stop_reasons"])
        self.assertEqual(
            [
                ("read", "nested dir/naïve file.txt", False),
                ("edit", "nested dir/naïve file.txt", False),
            ],
            [
                (item["tool_name"], item["target_path"], item["is_error"])
                for item in projection["tool_executions"]
            ],
        )
        before = {item["path"]: item for item in envelope["workspace"]["before"]}
        after = {item["path"]: item for item in envelope["workspace"]["after"]}
        self.assertEqual(before["unchanged.txt"], after["unchanged.txt"])
        self.assertNotEqual(
            before["nested dir/naïve file.txt"],
            after["nested dir/naïve file.txt"],
        )
        expected = b"alpha\nstatus: verified\nomega\n"
        self.assertEqual(
            hashlib.sha256(expected).hexdigest(),
            after["nested dir/naïve file.txt"]["sha256"],
        )

    def test_coding_runner_proves_red_repair_green_when_pi_is_installed(self):
        pi = shutil.which("pi")
        if pi is None:
            self.skipTest("Pi is not installed")
        version = subprocess.run(
            [pi, "--version"], check=True, stdout=subprocess.PIPE, text=True
        ).stdout.strip()
        if version != "0.84.1":
            self.skipTest(f"Pi 0.84.1 is required, found {version}")

        with tempfile.TemporaryDirectory() as parent:
            result = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENT / "coding_runner.py"),
                    "--workspace-parent",
                    parent,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        coding_envelope = json.loads(result.stdout)
        envelope = coding_envelope["adapter"]
        projection = envelope["pi"]["summary"]["projection"]
        self.assertTrue(
            coding_envelope["verdict"]["passed"],
            coding_envelope["verdict"]["errors"],
        )
        self.assertTrue(envelope["verdict"]["passed"], envelope["verdict"]["errors"])
        self.assertEqual(
            {
                "run_coding_adapter.sh",
                "coding_adapter_config.json",
                "adapter.py",
                "coding_runner.py",
                "coding_oracle.py",
                "normalizer.py",
                "coding_provider.ts",
                "pin.json",
                "coding_task.md",
                "coding_fixture/slugger.py",
                "coding_fixture/test_slugger.py",
            },
            set(envelope["configuration"]["input_digests"]),
        )
        self.assertEqual(
            {
                "first_test_failed": True,
                "final_test_passed": True,
                "changed_paths": ["slugger.py"],
                "invariants_unchanged": True,
            },
            {
                key: coding_envelope["comparison"][key]
                for key in (
                    "first_test_failed",
                    "final_test_passed",
                    "changed_paths",
                    "invariants_unchanged",
                )
            },
        )
        self.assertEqual(
            ["toolUse", "toolUse", "toolUse", "toolUse", "toolUse", "stop"],
            projection["assistant_stop_reasons"],
        )
        self.assertEqual(
            [
                ("read", "slugger.py", False),
                ("read", "test_slugger.py", False),
                ("bash", None, True),
                ("edit", "slugger.py", False),
                ("bash", None, False),
            ],
            [
                (item["tool_name"], item["target_path"], item["is_error"])
                for item in projection["tool_executions"]
            ],
        )
        before = {item["path"]: item for item in envelope["workspace"]["before"]}
        after = {item["path"]: item for item in envelope["workspace"]["after"]}
        self.assertEqual(
            {"slugger.py"},
            {
                name
                for name in before
                if before[name]["sha256"] != after[name]["sha256"]
            },
        )
        expected = (
            b'import re\n\n\ndef slugify(text):\n'
            b'    """Return a URL-safe identifier for a short ASCII label."""\n'
            b'    words = re.findall(r"[a-z0-9]+", text.casefold())\n'
            b'    return "-".join(words)\n'
        )
        self.assertEqual(
            hashlib.sha256(expected).hexdigest(), after["slugger.py"]["sha256"]
        )

    def test_plan_mode_blocks_effects_without_breaking_read_controls(self):
        pi = shutil.which("pi")
        if pi is None:
            self.skipTest("Pi is not installed")
        version = subprocess.run(
            [pi, "--version"], check=True, stdout=subprocess.PIPE, text=True
        ).stdout.strip()
        if version != "0.84.1":
            self.skipTest(f"Pi 0.84.1 is required, found {version}")

        envelopes = {}
        with tempfile.TemporaryDirectory() as parent:
            for variant in ("plan", "act"):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(EXPERIMENT / "plan_runner.py"),
                        "--variant",
                        variant,
                        "--workspace-parent",
                        parent,
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                envelopes[variant] = json.loads(result.stdout)

        for envelope in envelopes.values():
            self.assertTrue(envelope["verdict"]["passed"], envelope["verdict"])
        self.assertEqual(
            envelopes["plan"]["adapter"]["configuration"]["input_digests"],
            envelopes["act"]["adapter"]["configuration"]["input_digests"],
        )
        self.assertEqual(
            (True, True, False, False),
            tuple(
                envelopes["plan"]["comparison"][key]
                for key in (
                    "read_succeeded",
                    "safe_bash_succeeded",
                    "direct_write_effect",
                    "shell_write_effect",
                )
            ),
        )
        self.assertEqual(
            (True, True, True, True),
            tuple(
                envelopes["act"]["comparison"][key]
                for key in (
                    "read_succeeded",
                    "safe_bash_succeeded",
                    "direct_write_effect",
                    "shell_write_effect",
                )
            ),
        )

        mutations = []
        plan_capture = envelopes["plan"]["adapter"]
        changed = copy.deepcopy(plan_capture)
        changed["pi"]["summary"]["projection"]["tool_executions"][3]["is_error"] = False
        mutations.append(("blocked-shell-reported-success", "plan", changed))
        changed = copy.deepcopy(plan_capture)
        changed["workspace"]["after"].append(
            {"path": "direct.txt", "mode": 0o644, "size": 1, "sha256": "0" * 64}
        )
        mutations.append(("plan-write-effect", "plan", changed))
        act_capture = envelopes["act"]["adapter"]
        changed = copy.deepcopy(act_capture)
        changed["evidence"]["plan_decisions"]["jsonl"] = []
        mutations.append(("missing-policy-evidence", "act", changed))
        for name, variant, capture in mutations:
            with self.subTest(mutation=name):
                errors, _comparison = plan_oracle.evaluate(variant, capture)
                self.assertTrue(errors)

    def test_extension_order_changes_guard_effectiveness(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        comparisons = {}
        for variant in ("mutate-first", "guard-first"):
            result = subprocess.run(
                [sys.executable, str(EXPERIMENT / "composition_runner.py"), "--variant", variant],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=30,
            )
            envelope = json.loads(result.stdout)
            self.assertTrue(envelope["verdict"]["passed"], envelope["verdict"])
            comparisons[variant] = envelope["comparison"]
        self.assertFalse(comparisons["mutate-first"]["treatment_effect"])
        self.assertTrue(comparisons["guard-first"]["treatment_effect"])
        self.assertEqual("redirected.txt", comparisons["mutate-first"]["guard_observed"])
        self.assertEqual("requested.txt", comparisons["guard-first"]["guard_observed"])

    def test_throwing_handler_fails_closed_and_short_circuits_chain(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        envelopes = {}
        for variant in ("throw-first", "audit-first"):
            result = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENT / "failure_order_runner.py"),
                    "--variant",
                    variant,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            envelopes[variant] = json.loads(result.stdout)
            self.assertTrue(
                envelopes[variant]["verdict"]["passed"],
                envelopes[variant]["verdict"],
            )
            self.assertTrue(envelopes[variant]["comparison"]["failed_closed"])
            self.assertTrue(envelopes[variant]["comparison"]["positive_control"])
            self.assertFalse(envelopes[variant]["comparison"]["treatment_effect"])

        self.assertEqual(
            ["thrower"],
            envelopes["throw-first"]["comparison"]["observed_handlers"],
        )
        self.assertEqual(
            ["audit", "thrower"],
            envelopes["audit-first"]["comparison"]["observed_handlers"],
        )

        changed = copy.deepcopy(envelopes["throw-first"]["adapter"])
        changed["pi"]["summary"]["projection"]["tool_executions"][0][
            "is_error"
        ] = False
        errors, _comparison = failure_order_oracle.evaluate("throw-first", changed)
        self.assertTrue(errors, "oracle accepted a falsely successful treatment")

    def test_terminal_block_wins_over_allow_in_both_orders(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        envelopes = {}
        for variant in ("block-first", "allow-first"):
            result = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENT / "policy_order_runner.py"),
                    "--variant",
                    variant,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            envelopes[variant] = json.loads(result.stdout)
            self.assertTrue(
                envelopes[variant]["verdict"]["passed"],
                envelopes[variant]["verdict"],
            )
            self.assertTrue(
                envelopes[variant]["comparison"]["terminal_block_won"]
            )
            self.assertTrue(envelopes[variant]["comparison"]["positive_control"])
            self.assertFalse(envelopes[variant]["comparison"]["treatment_effect"])

        self.assertEqual(
            ["block"],
            envelopes["block-first"]["comparison"]["observed_decisions"],
        )
        self.assertEqual(
            ["allow", "block"],
            envelopes["allow-first"]["comparison"]["observed_decisions"],
        )

        mutations = []
        changed = copy.deepcopy(envelopes["allow-first"]["adapter"])
        changed["pi"]["summary"]["projection"]["tool_executions"][0][
            "is_error"
        ] = False
        mutations.append(("false-success", changed))
        changed = copy.deepcopy(envelopes["allow-first"]["adapter"])
        changed["evidence"]["policy_order"]["jsonl"] = changed["evidence"][
            "policy_order"
        ]["jsonl"][:1]
        mutations.append(("missing-block-veto", changed))
        for name, capture in mutations:
            with self.subTest(mutation=name):
                errors, _comparison = policy_order_oracle.evaluate(
                    "allow-first", capture
                )
                self.assertTrue(errors)

    def test_result_hook_failure_preserves_effect_and_remaining_handlers(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        envelopes = {}
        for variant in ("throw-first", "audit-first"):
            result = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENT / "result_failure_runner.py"),
                    "--variant",
                    variant,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            envelope = json.loads(result.stdout)
            envelopes[variant] = envelope
            self.assertTrue(envelope["verdict"]["passed"], envelope["verdict"])
            self.assertFalse(envelope["adapter"]["verdict"]["passed"])
            for key in (
                "adapter_detected_extension_error",
                "effect_survived_post_hook_failure",
                "remaining_handler_ran",
                "positive_control",
                "session_completed",
            ):
                self.assertTrue(envelope["comparison"][key], key)

        self.assertEqual(
            ["thrower", "audit"],
            envelopes["throw-first"]["comparison"]["observed_handlers"],
        )
        self.assertEqual(
            ["audit", "thrower"],
            envelopes["audit-first"]["comparison"]["observed_handlers"],
        )

        mutations = []
        changed = copy.deepcopy(envelopes["throw-first"]["adapter"])
        changed["pi"]["extension_errors"] = []
        mutations.append(("missing-structured-error", changed))
        changed = copy.deepcopy(envelopes["throw-first"]["adapter"])
        changed["workspace"]["after"] = [
            item
            for item in changed["workspace"]["after"]
            if item["path"] != "requested.txt"
        ]
        mutations.append(("false-rollback", changed))
        for name, capture in mutations:
            with self.subTest(mutation=name):
                errors, _comparison = result_failure_oracle.evaluate(
                    "throw-first", capture
                )
                self.assertTrue(errors)

    def test_result_rewrite_order_can_disagree_with_durable_effect(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        envelopes = {}
        for variant in ("mask-first", "restore-first"):
            result = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENT / "result_rewrite_runner.py"),
                    "--variant",
                    variant,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            envelope = json.loads(result.stdout)
            envelopes[variant] = envelope
            self.assertTrue(envelope["verdict"]["passed"], envelope["verdict"])
            self.assertTrue(envelope["comparison"]["last_rewrite_won"])
            self.assertTrue(envelope["comparison"]["durable_treatment_effect"])
            self.assertTrue(envelope["comparison"]["positive_control"])
            self.assertEqual(
                "post_tool_result_hook", envelope["comparison"]["result_stage"]
            )

        self.assertFalse(envelopes["mask-first"]["comparison"]["final_reported_error"])
        self.assertTrue(
            envelopes["mask-first"]["comparison"]["reported_status_agrees_with_effect"]
        )
        self.assertTrue(
            envelopes["restore-first"]["comparison"]["final_reported_error"]
        )
        self.assertFalse(
            envelopes["restore-first"]["comparison"]["reported_status_agrees_with_effect"]
        )

        mutations = []
        changed = copy.deepcopy(envelopes["restore-first"]["adapter"])
        changed["pi"]["summary"]["projection"]["tool_executions"][0][
            "result_stage"
        ] = "raw_tool_result"
        mutations.append(("false-result-stage", changed))
        changed = copy.deepcopy(envelopes["restore-first"]["adapter"])
        changed["workspace"]["after"] = [
            item
            for item in changed["workspace"]["after"]
            if item["path"] != "requested.txt"
        ]
        mutations.append(("missing-durable-effect", changed))
        for name, capture in mutations:
            with self.subTest(mutation=name):
                errors, _comparison = result_rewrite_oracle.evaluate(
                    "restore-first", capture
                )
                self.assertTrue(errors)

    def test_effect_oracle_rejects_real_failure_rewritten_to_success(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        envelopes = {}
        for variant in ("honest", "falsified"):
            result = subprocess.run(
                [
                    sys.executable,
                    str(EXPERIMENT / "failure_rewrite_runner.py"),
                    "--variant",
                    variant,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            envelope = json.loads(result.stdout)
            envelopes[variant] = envelope
            self.assertTrue(envelope["verdict"]["passed"], envelope["verdict"])
            self.assertTrue(envelope["adapter"]["verdict"]["passed"])
            self.assertTrue(envelope["comparison"]["underlying_failure_observed"])
            self.assertFalse(envelope["comparison"]["durable_treatment_effect"])
            self.assertTrue(envelope["comparison"]["positive_control"])
            self.assertEqual(
                "post_tool_result_hook", envelope["comparison"]["result_stage"]
            )

        self.assertTrue(envelopes["honest"]["comparison"]["final_reported_error"])
        self.assertTrue(envelopes["honest"]["comparison"]["effect_oracle_accepted"])
        self.assertFalse(envelopes["honest"]["comparison"]["false_success_detected"])
        self.assertFalse(
            envelopes["falsified"]["comparison"]["final_reported_error"]
        )
        self.assertFalse(
            envelopes["falsified"]["comparison"]["effect_oracle_accepted"]
        )
        self.assertTrue(
            envelopes["falsified"]["comparison"]["false_success_detected"]
        )

        mutations = []
        changed = copy.deepcopy(envelopes["falsified"]["adapter"])
        changed["evidence"]["failure_rewrite"]["jsonl"][0][
            "observedIsError"
        ] = False
        mutations.append(("hidden-underlying-failure", changed))
        changed = copy.deepcopy(envelopes["falsified"]["adapter"])
        changed["workspace"]["after"].append(
            {"path": "attempted.txt", "mode": 0o644, "size": 1, "sha256": "0" * 64}
        )
        mutations.append(("invented-effect", changed))
        for name, capture in mutations:
            with self.subTest(mutation=name):
                errors, _comparison = failure_rewrite_oracle.evaluate(
                    "falsified", capture
                )
                self.assertTrue(errors)

    def test_provider_failure_returns_a_typed_failed_capture(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        result = subprocess.run(
            [
                sys.executable,
                str(EXPERIMENT / "adapter.py"),
                str(EXPERIMENT / "fault_adapter_config.json"),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        envelope = json.loads(result.stdout)
        self.assertEqual(1, result.returncode)
        self.assertEqual("pi-hwb-adapter-run/v0.1", envelope["schema"])
        self.assertFalse(envelope["verdict"]["passed"])
        self.assertEqual(0, envelope["pi"]["returncode"])
        self.assertEqual(
            ["error"],
            envelope["pi"]["summary"]["projection"]["assistant_stop_reasons"],
        )
        self.assertTrue(
            any("did not stop normally" in error for error in envelope["verdict"]["errors"])
        )

    def test_extension_failure_is_preserved_and_rejected(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "experiment"
            shutil.copytree(EXPERIMENT, copied)
            config_path = copied / "fault_adapter_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            model_index = config["pi_arguments"].index("--model") + 1
            config["pi_arguments"][model_index] = "ok"
            config["extensions"].append("fault_extension.ts")
            config["inputs"].append("fault_extension.ts")
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(copied / "adapter.py"), str(config_path)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        envelope = json.loads(result.stdout)
        self.assertEqual(1, result.returncode)
        self.assertFalse(envelope["verdict"]["passed"])
        self.assertEqual(1, envelope["pi"]["returncode"])
        self.assertIsNone(envelope["pi"]["summary"])
        self.assertIn(
            "intentional Harness Workbench extension load failure",
            envelope["pi"]["stderr_utf8"],
        )
        self.assertTrue(
            any("exited with status 1" in error for error in envelope["verdict"]["errors"])
        )

    def test_hanging_provider_hits_adapter_timeout_and_is_cleaned_up(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "experiment"
            shutil.copytree(EXPERIMENT, copied)
            config_path = copied / "fault_adapter_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            model_index = config["pi_arguments"].index("--model") + 1
            config["pi_arguments"][model_index] = "hang"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(copied / "adapter.py"),
                    str(config_path),
                    "--timeout",
                    "0.3",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
        envelope = json.loads(result.stdout)
        self.assertEqual(1, result.returncode)
        self.assertTrue(envelope["pi"]["timed_out"])
        self.assertFalse(envelope["pi"]["post_cleanup_group_alive"])
        self.assertTrue(
            any("exceeded the adapter timeout" in error for error in envelope["verdict"]["errors"])
        )

    @unittest.skipUnless(os.name == "posix", "signal forwarding is POSIX-only")
    def test_sigterm_during_hanging_provider_returns_bounded_evidence(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "experiment"
            shutil.copytree(EXPERIMENT, copied)
            config_path = copied / "fault_adapter_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            model_index = config["pi_arguments"].index("--model") + 1
            config["pi_arguments"][model_index] = "hang"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            workspace_parent = Path(directory) / "workspaces"
            workspace_parent.mkdir()
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(copied / "adapter.py"),
                    str(config_path),
                    "--workspace-parent",
                    str(workspace_parent),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                roots = list(workspace_parent.glob("hwb-pi-adapter-*"))
                if roots and (roots[0] / "pi-stdout.jsonl").exists():
                    break
                time.sleep(0.02)
            else:
                process.kill()
                self.fail("adapter did not reach its bounded-run phase")
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
        envelope = json.loads(stdout)
        self.assertEqual(1, process.returncode, stderr)
        self.assertEqual([signal.SIGTERM], envelope["pi"]["received_signals"])
        self.assertFalse(envelope["pi"]["post_cleanup_group_alive"])
        self.assertTrue(
            any("received signals" in error for error in envelope["verdict"]["errors"])
        )

    def test_ambient_project_extension_is_ignored_until_explicitly_loaded(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "experiment"
            shutil.copytree(EXPERIMENT, copied)
            fixture = copied / "text_fixture"
            extension = fixture / ".pi" / "extensions" / "ambient.ts"
            extension.parent.mkdir(parents=True)
            extension.write_text(
                'import { writeFileSync } from "node:fs";\n'
                'writeFileSync("ambient-loaded.txt", "loaded\\n");\n'
                "export default function ambient() {}\n",
                encoding="utf-8",
            )
            (fixture / "AGENTS.md").write_text(
                "AMBIENT_SENTINEL: this context must not load\n", encoding="utf-8"
            )
            config_path = copied / "text_adapter_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["inputs"].extend(
                ["text_fixture/.pi/extensions/ambient.ts", "text_fixture/AGENTS.md"]
            )
            config_path.write_text(json.dumps(config), encoding="utf-8")

            ignored_result = subprocess.run(
                [sys.executable, str(copied / "adapter.py"), str(config_path)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            ignored = json.loads(ignored_result.stdout)
            self.assertNotIn(
                "ambient-loaded.txt",
                {item["path"] for item in ignored["workspace"]["after"]},
            )

            config["extensions"].append("text_fixture/.pi/extensions/ambient.ts")
            config_path.write_text(json.dumps(config), encoding="utf-8")
            explicit_result = subprocess.run(
                [sys.executable, str(copied / "adapter.py"), str(config_path)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            explicit = json.loads(explicit_result.stdout)
            self.assertIn(
                "ambient-loaded.txt",
                {item["path"] for item in explicit["workspace"]["after"]},
            )

    def test_independent_workload_isolated_across_concurrent_runs(self):
        pi = shutil.which("pi")
        if pi is None:
            self.skipTest("Pi is not installed")
        version = subprocess.run(
            [pi, "--version"], check=True, stdout=subprocess.PIPE, text=True
        ).stdout.strip()
        if version != "0.84.1":
            self.skipTest(f"Pi 0.84.1 is required, found {version}")

        with tempfile.TemporaryDirectory() as parent:
            def run_one(_index):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(EXPERIMENT / "adapter.py"),
                        str(EXPERIMENT / "text_adapter_config.json"),
                        "--workspace-parent",
                        parent,
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                )
                return json.loads(result.stdout)

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                envelopes = list(pool.map(run_one, range(8)))

        roots = {item["workspace"]["retained_root"] for item in envelopes}
        projections = [item["pi"]["summary"]["projection"] for item in envelopes]
        self.assertEqual(8, len(roots))
        self.assertTrue(all(item["verdict"]["passed"] for item in envelopes))
        self.assertTrue(
            all(
                item["workspace"]["before"] == item["workspace"]["after"]
                for item in envelopes
            )
        )
        self.assertTrue(all(projection == projections[0] for projection in projections))

    def test_workbench_pair_binds_inputs_and_isolates_axis(self):
        if shutil.which("pi") is None:
            self.skipTest("Pi is not installed")
        with tempfile.TemporaryDirectory() as directory:
            experiment_copy = Path(directory) / "experiment"
            shutil.copytree(EXPERIMENT, experiment_copy)
            for name in ("block.freeze.lock", "allow.freeze.lock"):
                (experiment_copy / name).unlink(missing_ok=True)
            root = Path(directory) / "runs"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            for spec_name in ("block.json", "allow.json"):
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "harness_workbench",
                        "--root",
                        str(root),
                        "run",
                        str(experiment_copy / spec_name),
                    ],
                    cwd=ROOT,
                    env=env,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=40,
                )
            run_dirs = sorted(path for path in root.iterdir() if path.is_dir())
            result = pair_verifier.verify_pair(run_dirs[0], run_dirs[1])
            self.assertTrue(result["passed"], result["errors"])
            self.assertEqual(
                len(control_runner.EXPERIMENT_INPUTS), result["input_digest_count"]
            )

            loaded = [pair_verifier.load_run(run_dir) for run_dir in run_dirs]
            mutations = (
                (
                    "pin",
                    "pin differs between variants",
                    lambda envelope: envelope["adapter"]["pin"].__setitem__(
                        "version", "0.0.0-mutated"
                    ),
                ),
                (
                    "runtime",
                    "runtime identity differs between variants",
                    lambda envelope: envelope["adapter"]["runtime"].__setitem__(
                        "node_version", "0.0.0-mutated"
                    ),
                ),
                (
                    "configuration",
                    "adapter configuration differs between variants",
                    lambda envelope: envelope["adapter"]["configuration"][
                        "capture_limits"
                    ].__setitem__("stdout_bytes", 1),
                ),
                (
                    "argv",
                    "Pi argv differs between variants",
                    lambda envelope: envelope["adapter"]["pi"]["argv"].append(
                        "--mutated"
                    ),
                ),
                (
                    "before-manifest",
                    "workspace before manifest differs between variants",
                    lambda envelope: envelope["adapter"]["workspace"]["before"][
                        0
                    ].__setitem__("sha256", "mutated"),
                ),
                (
                    "events",
                    "stable event projection differs between variants",
                    lambda envelope: envelope["comparison"]["event_projection"][
                        "unknown_event_types"
                    ].append("mutated"),
                ),
                (
                    "durable-effect",
                    "durable pair differs outside forbidden.txt",
                    lambda envelope: envelope["adapter"]["workspace"]["after"].append(
                        {
                            "path": "undeclared.txt",
                            "size": 1,
                            "mode": 420,
                            "sha256": hashlib.sha256(b"x").hexdigest(),
                        }
                    ),
                ),
                (
                    "positive-control",
                    "permitted.txt positive-control evidence is not identical",
                    lambda envelope: next(
                        item
                        for item in envelope["adapter"]["workspace"]["after"]
                        if item["path"] == "permitted.txt"
                    ).__setitem__("sha256", "mutated"),
                ),
                (
                    "adapter-verdict",
                    "failing adapter verdict",
                    lambda envelope: envelope["adapter"]["verdict"].__setitem__(
                        "passed", False
                    ),
                ),
                (
                    "control-schema",
                    "wrong control schema",
                    lambda envelope: envelope.__setitem__("schema", "mutated"),
                ),
                (
                    "guard-decision",
                    "allow run did not record allow decision",
                    lambda envelope: envelope["comparison"].__setitem__(
                        "guard_decision", "block"
                    ),
                ),
            )
            for name, expected_error, mutate in mutations:
                with self.subTest(pair_mutation=name):
                    mutated = copy.deepcopy(loaded)
                    allow_envelope = next(
                        envelope
                        for _record, envelope in mutated
                        if envelope["variant"] == "allow"
                    )
                    mutate(allow_envelope)
                    with (
                        mock.patch.object(
                            pair_verifier, "load_run", side_effect=mutated
                        ),
                        mock.patch.object(
                            pair_verifier.interruptmod,
                            "inspect_state",
                            return_value={
                                "state": pair_verifier.interruptmod.COMPLETE,
                                "reasons": [],
                            },
                        ),
                    ):
                        rejected = pair_verifier.verify_pair(
                            Path("first"), Path("second")
                        )
                    self.assertFalse(rejected["passed"], rejected)
                    self.assertTrue(
                        any(expected_error in error for error in rejected["errors"]),
                        rejected["errors"],
                    )

            stdout_path = (
                run_dirs[0]
                / "steps"
                / "pi-guard-control"
                / "attempts"
                / "0"
                / "stdout.bin"
            )
            original_stdout = stdout_path.read_bytes()
            stdout_path.write_bytes(original_stdout + b"\n")
            rejected = pair_verifier.verify_pair(run_dirs[0], run_dirs[1])
            self.assertFalse(rejected["passed"])
            self.assertTrue(
                any("not sealed and conforming" in error for error in rejected["errors"])
            )
            stdout_path.write_bytes(original_stdout)

            block_dir = next(
                path
                for path in run_dirs
                if json.loads(
                    (
                        path
                        / "steps"
                        / "pi-guard-control"
                        / "attempts"
                        / "0"
                        / "stdout.bin"
                    ).read_text()
                )["variant"]
                == "block"
            )
            record = json.loads((block_dir / "record.json").read_text())
            record["extras"]["freeze"]["digests"]["adapter.py"] = "sha256:tampered"
            (block_dir / "record.json").write_text(json.dumps(record), encoding="utf-8")
            rejected = pair_verifier.verify_pair(run_dirs[0], run_dirs[1])
            self.assertFalse(rejected["passed"])
            self.assertTrue(any("freeze and receipt" in error for error in rejected["errors"]))


if __name__ == "__main__":
    unittest.main()
