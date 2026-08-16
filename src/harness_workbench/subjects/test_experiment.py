"""Deterministic tests for the cross-harness adapter boundary."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import adapters
import compare as comparator
import runner as subject_runner
import usage_probe
from harness_workbench.capture import (
    Bounded,
    capture_bytes,
    credential_values,
    manifest,
    redact_bytes,
    run_bounded,
)
from oracles import EXPECTED_CONTENT, outcome, repair_outcome


def jsonl(*events: dict) -> bytes:
    return b"".join(
        json.dumps(event, sort_keys=True).encode("utf-8") + b"\n"
        for event in events
    )


class CommonTests(unittest.TestCase):
    def test_expected_effect_is_unambiguous(self) -> None:
        self.assertEqual(EXPECTED_CONTENT, b"cross-harness control\n")
        self.assertEqual(len(EXPECTED_CONTENT), 22)
        self.assertIn("exactly the 22 ASCII bytes", adapters.PROMPT)

    def test_jsonl_rejects_non_object_and_malformed_line(self) -> None:
        # Through the tree's binding, not the primitive's permissive default:
        # objects-only is this experiment's contract, so this is what has to
        # hold for every normalizer downstream of it.
        events, errors = adapters.parse_jsonl_objects(
            b'{"ok":true}\n[]\nnot-json\n'
        )
        self.assertEqual(events, [{"ok": True}])
        self.assertEqual(len(errors), 2)

    def test_outcome_requires_exact_effect_and_unchanged_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "task.md").write_text("task", encoding="utf-8")
            (root / "hook.py").write_text("hook", encoding="utf-8")
            before = manifest(root)
            (root / "shared.txt").write_bytes(EXPECTED_CONTENT)
            self.assertTrue(outcome(before, manifest(root))["passed"])
            (root / "shared.txt").write_bytes(EXPECTED_CONTENT + b".")
            self.assertFalse(outcome(before, manifest(root))["passed"])

    def test_specs_bind_the_same_complete_input_set(self) -> None:
        for subject in ("claude", "codex", "deepseek", "hermes", "pi"):
            spec = json.loads(
                (adapters.HERE / f"{subject}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(tuple(spec["steps"][0]["inputs"]), adapters.INPUTS)
            # Order is load-bearing, not cosmetic: the last-declared wrap ends
            # up outermost, so `sample` must follow anything that would
            # otherwise enclose it. `timing` is an observe and contends for no
            # seam.
            self.assertEqual(
                [feature["name"] for feature in spec["features"]],
                ["freeze", "receipt", "retry", "sample", "timing"],
            )
            by_name = {f["name"]: f for f in spec["features"]}
            self.assertEqual(by_name["sample"]["config"]["n"], 3)
            self.assertEqual(by_name["retry"]["config"]["max"], 2)

    def test_retry_is_nested_inside_sample_not_around_it(self) -> None:
        # Order is the experiment, not a formatting choice. The last-declared
        # wrap is outermost, so `retry` before `sample` composes as
        # sample(retry(step)): a failed draw is retried on its own. Reversed,
        # retry(sample(step)) re-runs the whole set when one draw failed --
        # discarding draws that were already valid and paying for them twice.
        for subject in ("claude", "codex", "deepseek", "hermes", "pi"):
            spec = json.loads(
                (adapters.HERE / f"{subject}.json").read_text(encoding="utf-8")
            )
            names = [feature["name"] for feature in spec["features"]]
            self.assertLess(names.index("retry"), names.index("sample"))

    def test_worst_case_subject_invocations_stay_bounded(self) -> None:
        # Nothing meters spend, so the bound has to come from the only two
        # numbers that multiply: draws and retries. 3 x 2 = 6 invocations per
        # spec worst case, 3 when nothing needs retrying. Stated as a test so
        # raising either number is a decision someone makes on purpose.
        spec = json.loads(
            (adapters.HERE / "claude.json").read_text(encoding="utf-8")
        )
        by_name = {f["name"]: f for f in spec["features"]}
        worst_case = by_name["sample"]["config"]["n"] * by_name["retry"]["config"]["max"]
        self.assertLessEqual(worst_case, 6)

    def test_repair_specs_bind_the_same_complete_input_set(self) -> None:
        for subject in ("claude", "codex", "deepseek", "hermes", "pi"):
            spec = json.loads(
                (adapters.HERE / f"repair_{subject}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                tuple(spec["steps"][0]["inputs"]), adapters.REPAIR_INPUTS
            )

    def test_streaming_capture_enforces_stdout_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_bounded(
                [sys.executable, "-c", 'print("x" * 10000)'],
                cwd=Path(directory),
                env=os.environ.copy(),
                timeout=2,
                stdout_limit=100,
                stderr_limit=100,
            )
        # The bound is read by name. An earlier version of this tree asserted
        # returncode 125, which a subject can also exit with on its own -- the
        # assertion could not tell a fired bound from an honest exit status.
        self.assertEqual(result.termination_reason, "stdout_limit")
        self.assertTrue(result.stdout_overflow)
        self.assertEqual(len(result.stdout), 100)
        self.assertGreater(result.stdout_source_bytes, len(result.stdout))

    def test_timeout_preserves_partial_effect_without_terminal_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_bounded(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import time; "
                    "Path('partial.txt').write_text('partial'); "
                    "print('started', flush=True); time.sleep(10)",
                ],
                cwd=root,
                env=os.environ.copy(),
                timeout=0.1,
            )
            self.assertEqual((root / "partial.txt").read_text(), "partial")
        self.assertEqual(result.termination_reason, "timeout")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"started\n")
        self.assertFalse(result.group_alive_after_cleanup)

    def test_escaped_child_pipe_cannot_hold_capture_loop_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            result = run_bounded(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys; "
                    "subprocess.Popen([sys.executable,'-c','import time;time.sleep(.4)'], "
                    "start_new_session=True); print('parent',flush=True)",
                ],
                cwd=Path(directory),
                env=os.environ.copy(),
                timeout=0.05,
                termination_grace=0.05,
            )
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.3)
        # The old tree escaped this only by burning the whole timeout, and then
        # recorded a timeout for a child that had already exited cleanly. The
        # primitive notices the child is gone and the pipe drained, so it
        # returns the real exit status and claims no bound. The escaped
        # grandchild is still not waited on -- that is what `elapsed` proves.
        self.assertIsNone(result.termination_reason)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"parent\n")

    def test_credentials_are_redacted_before_serialization(self) -> None:
        secret = 'credential-"quoted"-value'
        values = credential_values({"HWB_TEST_SECRET": secret, "NORMAL": "visible"})
        raw = ("plain=" + secret + " json=" + json.dumps(secret)[1:-1]).encode()
        stored, count = redact_bytes(raw, values)
        self.assertNotIn(secret.encode(), stored)
        self.assertNotIn(json.dumps(secret)[1:-1].encode(), stored)
        self.assertGreaterEqual(count, 2)
        captured = capture_bytes(raw, redactions=values)
        serialized = json.dumps(captured)
        self.assertNotIn(secret, serialized)
        self.assertGreater(captured["redaction_count"], 0)

    def test_a_non_ascii_secret_survives_json_escaping(self) -> None:
        # The inversion of the encoding-variant fix. `json.dumps` escapes any
        # non-ASCII byte to \uXXXX BY DEFAULT -- including this project's own
        # Hermes hook -- so a secret that contains one reached sealed evidence
        # while `redaction_count: 0` said no secret had been present.
        secret = "sécret-token-with-ünicode"
        values = credential_values({"HWB_TEST_TOKEN": secret})
        self.assertEqual(values, (secret,))
        escaped = json.dumps({"command": f"export KEY={secret}"}).encode("utf-8")
        self.assertIn(b"\\u00e9", escaped)  # the form that used to slip
        stored, count = redact_bytes(escaped, values)
        self.assertNotIn(b"\\u00e9", stored)
        self.assertNotIn(secret.encode("utf-8"), stored)
        self.assertEqual(count, 1)
        captured = capture_bytes(escaped, redactions=values)
        self.assertNotIn("u00e9", json.dumps(captured))
        self.assertGreater(captured["redaction_count"], 0)

    def test_the_hermes_hook_scrubber_is_not_handed_an_empty_list(self) -> None:
        # The second layer, which was switched off. The hook scrubs values
        # before serialization, so it is the one place an encoding cannot
        # defeat -- but only if it is told what to scrub.
        source = (adapters.HERE / "adapters.py").read_text(encoding="utf-8")
        self.assertIn(
            'environment["HWB_REDACT_VALUES_JSON"] = json.dumps(list(redactions))',
            source,
        )
        self.assertNotIn('environment["HWB_REDACT_VALUES_JSON"] = "[]"', source)

    def test_hook_redacts_and_refuses_oversized_evidence(self) -> None:
        secret = "hook-secret-value"
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "hooks.jsonl"
            environment = os.environ.copy()
            environment.update({
                "HWB_HERMES_HOOK_EVIDENCE": str(evidence),
                "HWB_HERMES_HOOK_MAX_BYTES": "4096",
                "HWB_REDACT_VALUES_JSON": json.dumps([secret]),
            })
            payload = json.dumps({"value": secret}).encode()
            accepted = subprocess.run(
                [sys.executable, str(adapters.HERE / "hook.py")],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
            self.assertEqual(accepted.returncode, 0)
            self.assertNotIn(secret, evidence.read_text())
            environment["HWB_HERMES_HOOK_MAX_BYTES"] = "1"
            refused = subprocess.run(
                [sys.executable, str(adapters.HERE / "hook.py")],
                input=b"{}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
            self.assertEqual(refused.returncode, 3)


class ClaudeNormalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path("/workspace")
        self.call = {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "id": "call-1",
                "name": "Write",
                "input": {
                    "file_path": "/workspace/shared.txt",
                    "content": EXPECTED_CONTENT.decode("utf-8"),
                },
            }]},
        }
        self.result = {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "call-1",
                "is_error": False,
                "content": "ok",
            }]},
        }
        self.terminal = {"type": "result", "subtype": "success", "is_error": False}

    def test_valid_lifecycle(self) -> None:
        lifecycle, errors = adapters._normalize_claude(
            jsonl(
                {"type": "system", "subtype": "init"},
                self.call,
                self.result,
                self.terminal,
            ),
            self.workspace,
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(lifecycle["tool_executions"]), 1)

    def test_duplicate_terminal_is_rejected(self) -> None:
        _, errors = adapters._normalize_claude(
            jsonl(
                {"type": "system", "subtype": "init"},
                self.call,
                self.result,
                self.terminal,
                self.terminal,
            ),
            self.workspace,
        )
        self.assertTrue(any("does not end" in error for error in errors))

    def test_duplicate_system_init_is_rejected_but_telemetry_is_allowed(self) -> None:
        init = {"type": "system", "subtype": "init"}
        telemetry = {"type": "system", "subtype": "thinking_tokens"}
        _, valid_errors = adapters._normalize_claude(
            jsonl(init, telemetry, self.call, self.result, self.terminal),
            self.workspace,
        )
        self.assertEqual(valid_errors, [])
        _, duplicate_errors = adapters._normalize_claude(
            jsonl(init, init, self.call, self.result, self.terminal),
            self.workspace,
        )
        self.assertTrue(any("system init" in error for error in duplicate_errors))

    def test_duplicate_tool_call_is_rejected(self) -> None:
        _, errors = adapters._normalize_claude(
            jsonl(
                {"type": "system", "subtype": "init"},
                self.call,
                self.call,
                self.result,
                self.terminal,
            ),
            self.workspace,
        )
        self.assertIn("duplicate Claude tool call: call-1", errors)


class CodexNormalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path("/workspace")
        self.started = {
            "type": "item.started",
            "item": {
                "id": "item-1",
                "type": "file_change",
                "changes": [{"path": "/workspace/shared.txt", "kind": "add"}],
                "status": "in_progress",
            },
        }
        self.completed = {
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": "file_change",
                "changes": [{"path": "/workspace/shared.txt", "kind": "add"}],
                "status": "completed",
            },
        }

    def stream(self, *middle: dict) -> bytes:
        return jsonl(
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "turn.started"},
            *middle,
            {"type": "turn.completed"},
        )

    def test_valid_lifecycle(self) -> None:
        lifecycle, errors = adapters._normalize_codex(
            self.stream(self.started, self.completed), self.workspace
        )
        self.assertEqual(errors, [])
        self.assertFalse(lifecycle["tool_executions"][0]["reported_error"])

    def test_tool_completion_without_start_is_rejected(self) -> None:
        _, errors = adapters._normalize_codex(
            self.stream(self.completed), self.workspace
        )
        self.assertIn("Codex tool completion has no start: item-1", errors)

    def test_duplicate_terminal_is_rejected(self) -> None:
        raw = self.stream(
            self.started, self.completed, {"type": "turn.completed"}
        )
        _, errors = adapters._normalize_codex(raw, self.workspace)
        self.assertTrue(any("does not end" in error for error in errors))

    def test_failed_native_tool_status_is_preserved(self) -> None:
        failed = json.loads(json.dumps(self.completed))
        failed["item"]["status"] = "failed"
        lifecycle, errors = adapters._normalize_codex(
            self.stream(self.started, failed), self.workspace
        )
        self.assertEqual(errors, [])
        self.assertTrue(lifecycle["tool_executions"][0]["reported_error"])


class HermesNormalizerTests(unittest.TestCase):
    def event(self, name: str, *, status: str | None = None) -> dict:
        extra = {"tool_call_id": "call-1"}
        if status is not None:
            extra["status"] = status
        return {
            "hook_event_name": name,
            "tool_name": "write_file",
            "tool_input": {
                "path": "/workspace/shared.txt",
                "content": EXPECTED_CONTENT.decode("utf-8"),
            },
            "extra": extra,
        }

    def test_valid_hook_pair_with_process_boundary(self) -> None:
        lifecycle, errors = adapters._normalize_hermes(
            b"done\n",
            jsonl(
                self.event("pre_tool_call"),
                self.event("post_tool_call", status="ok"),
            ),
            Path("/workspace"),
            0,
        )
        self.assertEqual(errors, [])
        execution = lifecycle["tool_executions"][0]
        self.assertFalse(execution["reported_error"])
        self.assertEqual(lifecycle["completeness"], "process_boundary_only")

    def test_out_of_order_hook_pair_is_rejected(self) -> None:
        _, errors = adapters._normalize_hermes(
            b"done\n",
            jsonl(
                self.event("post_tool_call", status="ok"),
                self.event("pre_tool_call"),
            ),
            Path("/workspace"),
            0,
        )
        self.assertIn("Hermes hook pair is out of order: call-1", errors)

    def test_outside_workspace_proposal_is_rejected(self) -> None:
        pre = self.event("pre_tool_call")
        post = self.event("post_tool_call", status="ok")
        pre["tool_input"]["path"] = "/outside/shared.txt"
        post["tool_input"]["path"] = "/outside/shared.txt"
        _, errors = adapters._normalize_hermes(
            b"done\n", jsonl(pre, post), Path("/workspace"), 0
        )
        self.assertEqual(
            errors,
            [
                "Hermes proposed an operation outside the disposable workspace: call-1"
            ],
        )

    def test_hook_error_is_preserved_without_invalidating_structure(self) -> None:
        lifecycle, errors = adapters._normalize_hermes(
            b"done\n",
            jsonl(
                self.event("pre_tool_call"),
                self.event("post_tool_call", status="error"),
            ),
            Path("/workspace"),
            0,
        )
        self.assertEqual(errors, [])
        self.assertTrue(lifecycle["tool_executions"][0]["reported_error"])


class DeepSeekNormalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path("/workspace")
        self.header = {
            "type": "session",
            "version": 0,
            "id": "session-1",
            "createdAt": 0,
            "cwd": "/workspace",
            "delegationDepth": 0,
        }
        self.turn_start = {
            "type": "turn/start",
            "seq": 0,
            "time": 0,
            "data": {"turn": 1},
        }
        self.context = {
            "type": "request/context",
            "seq": 1,
            "time": 0,
            "data": {
                "provider": "workbench-ollama",
                "model": "qwen3.5:9b",
            },
        }
        self.call = {
            "type": "tool/call",
            "seq": 2,
            "time": 0,
            "data": {
                "turn": 1,
                "step": 1,
                "callId": "call-1",
                "name": "write",
                "arguments": json.dumps({
                    "file_path": "/workspace/shared.txt",
                    "content": EXPECTED_CONTENT.decode("utf-8"),
                }),
            },
        }
        self.result = {
            "type": "tool/result",
            "seq": 3,
            "time": 0,
            "data": {
                "turn": 1,
                "step": 1,
                "message": {
                    "content": [{
                        "type": "tool-result",
                        "toolCallId": "call-1",
                        "content": [{"type": "text", "text": "ok"}],
                        "isError": False,
                    }],
                },
            },
        }
        self.terminal = {
            "type": "turn/end",
            "seq": 4,
            "time": 0,
            "data": {"turn": 1, "reason": {"kind": "completed"}},
        }

    def stream(self, *events: dict) -> bytes:
        return jsonl(self.header, *events)

    def normalize(self, raw: bytes) -> tuple[dict, list[str]]:
        return adapters._normalize_deepseek(
            raw,
            self.workspace,
            0,
            "workbench-ollama",
            "qwen3.5:9b",
        )

    def test_valid_persisted_lifecycle(self) -> None:
        lifecycle, errors = self.normalize(self.stream(
            self.turn_start, self.context, self.call, self.result, self.terminal
        ))
        self.assertEqual(errors, [])
        self.assertEqual(lifecycle["terminal"]["status"], "completed")
        self.assertFalse(lifecycle["tool_executions"][0]["reported_error"])
        self.assertEqual(
            lifecycle["tool_executions"][0]["acquisition"],
            "native_persisted_jsonl",
        )

    def test_log_scoped_permission_prelude_is_allowed(self) -> None:
        prelude = {
            "type": "permission/preset",
            "seq": 0,
            "time": 0,
            "data": {"preset": "workbench"},
        }
        events = [
            json.loads(json.dumps(event))
            for event in (
                self.turn_start,
                self.context,
                self.call,
                self.result,
                self.terminal,
            )
        ]
        for event in events:
            event["seq"] += 1
        _, errors = self.normalize(self.stream(prelude, *events))
        self.assertEqual(errors, [])

    def test_duplicate_terminal_is_rejected(self) -> None:
        first = json.loads(json.dumps(self.terminal))
        first["seq"] = 4
        second = json.loads(json.dumps(self.terminal))
        second["seq"] = 5
        _, errors = self.normalize(self.stream(
            self.turn_start, self.context, self.call, self.result, first, second
        ))
        self.assertIn(
            "DeepSeek log does not contain one complete ordered turn", errors
        )

    def test_noncontiguous_sequence_is_rejected(self) -> None:
        context = json.loads(json.dumps(self.context))
        context["seq"] = 9
        _, errors = self.normalize(self.stream(
            self.turn_start, context, self.call, self.result, self.terminal
        ))
        self.assertTrue(any("not contiguous" in error for error in errors))

    def test_orphan_result_is_rejected(self) -> None:
        result = json.loads(json.dumps(self.result))
        result["seq"] = 2
        terminal = json.loads(json.dumps(self.terminal))
        terminal["seq"] = 3
        _, errors = self.normalize(self.stream(
            self.turn_start, self.context, result, terminal
        ))
        self.assertIn("DeepSeek tool result has no call: call-1", errors)

    def test_result_before_call_is_rejected(self) -> None:
        result = json.loads(json.dumps(self.result))
        result["seq"] = 2
        call = json.loads(json.dumps(self.call))
        call["seq"] = 3
        _, errors = self.normalize(self.stream(
            self.turn_start, self.context, result, call, self.terminal
        ))
        self.assertIn("DeepSeek tool result precedes its call: call-1", errors)

    def test_malformed_arguments_are_rejected(self) -> None:
        call = json.loads(json.dumps(self.call))
        call["data"]["arguments"] = "not-json"
        _, errors = self.normalize(self.stream(
            self.turn_start, self.context, call, self.result, self.terminal
        ))
        self.assertIn(
            "DeepSeek tool call arguments are not an object: call-1", errors
        )

    def test_outside_workspace_proposal_is_rejected(self) -> None:
        call = json.loads(json.dumps(self.call))
        arguments = json.loads(call["data"]["arguments"])
        arguments["file_path"] = "/outside/shared.txt"
        call["data"]["arguments"] = json.dumps(arguments)
        _, errors = self.normalize(self.stream(
            self.turn_start, self.context, call, self.result, self.terminal
        ))
        self.assertIn(
            "DeepSeek proposed an operation outside workspace: call-1", errors
        )

    def test_provider_model_mismatch_is_rejected(self) -> None:
        context = json.loads(json.dumps(self.context))
        context["data"]["model"] = "different-model"
        _, errors = self.normalize(self.stream(
            self.turn_start, context, self.call, self.result, self.terminal
        ))
        self.assertIn(
            "DeepSeek provider/model context disagrees with the pin", errors
        )

    def test_shell_exit_code_is_projected_separately_from_tool_error(self) -> None:
        call = json.loads(json.dumps(self.call))
        call["data"]["name"] = "bash"
        call["data"]["arguments"] = json.dumps({
            "command": "python3.11 -m unittest -v",
        })
        result = json.loads(json.dumps(self.result))
        result["data"]["message"]["content"][0]["content"] = [{
            "type": "text",
            "text": "FAILED\n[exit code: 1]",
        }]
        lifecycle, errors = self.normalize(self.stream(
            self.turn_start, self.context, call, result, self.terminal
        ))
        self.assertEqual(errors, [])
        execution = lifecycle["tool_executions"][0]
        self.assertFalse(execution["reported_error"])
        self.assertEqual(execution["operation_exit_code"], 1)


class RepairOutcomeTests(unittest.TestCase):
    @staticmethod
    def process(returncode: int) -> Bounded:
        return Bounded(
            argv=["python3.11", "-m", "unittest", "-v"],
            returncode=returncode,
            termination_reason=None,
            stdout=b"",
            stderr=b"",
            stdout_source_bytes=0,
            stderr_source_bytes=0,
            stdout_overflow=False,
            stderr_overflow=False,
            group_alive_before_cleanup=False,
            group_alive_after_cleanup=False,
        )

    def test_exact_repair_boundary_and_subject_sequence_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("hook.py", "repair_task.md", "test_slugger.py"):
                (root / name).write_text(name, encoding="utf-8")
            (root / "slugger.py").write_text("broken", encoding="utf-8")
            before = manifest(root)
            (root / "slugger.py").write_text("fixed", encoding="utf-8")
            after = manifest(root)
        executions = [
            {
                "effect_kind": "command",
                "operation": "python_unittest_v",
                "reported_error": False,
                "operation_exit_code": 1,
            },
            {"effect_kind": "write", "reported_error": False},
            {
                "effect_kind": "command",
                "operation": "python_unittest_v",
                "reported_error": False,
                "operation_exit_code": 0,
            },
        ]
        result = repair_outcome(
            before,
            after,
            initial_test=self.process(1),
            final_test=self.process(0),
            tool_executions=executions,
        )
        self.assertTrue(result["passed"])

    def test_green_effect_without_subject_red_green_sequence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("hook.py", "repair_task.md", "test_slugger.py"):
                (root / name).write_text(name, encoding="utf-8")
            (root / "slugger.py").write_text("broken", encoding="utf-8")
            before = manifest(root)
            (root / "slugger.py").write_text("fixed", encoding="utf-8")
            after = manifest(root)
        result = repair_outcome(
            before,
            after,
            initial_test=self.process(1),
            final_test=self.process(0),
            tool_executions=[
                {"effect_kind": "write", "reported_error": False},
            ],
        )
        self.assertFalse(result["passed"])
        self.assertIn(
            "subject evidence lacks red-command -> write -> green-command",
            result["errors"],
        )


class PinTests(unittest.TestCase):
    def test_expected_effect_digest_is_stable(self) -> None:
        self.assertEqual(
            hashlib.sha256(EXPECTED_CONTENT).hexdigest(),
            "2e8552d04a55edf3110197d2dfdaf76a77c9247b76f1438b0c153cf0245d4d2e",
        )


class UsageGateTests(unittest.TestCase):
    """The budget control, exercised offline against an injected reader.

    Every case here is a refusal case. A gate that has only ever been seen to
    allow is indistinguishable from no gate, and this one guards spending.
    """

    @staticmethod
    def payload(rolling: int, weekly: int, monthly: int, *, resets: str = "T1") -> dict:
        return {
            name: {"percent": pct, "status": "ok", "resetsAt": resets}
            for name, pct in (
                ("rolling", rolling), ("weekly", weekly), ("monthly", monthly)
            )
        }

    def reading(self, rolling: int, weekly: int, monthly: int, **kw) -> dict:
        return usage_probe.snapshot(
            reader=lambda: self.payload(rolling, weekly, monthly, **kw)
        )

    def test_a_window_at_its_line_is_refused_not_rounded_down(self) -> None:
        # `>=`, deliberately. "You may spend right up to the line" is how a
        # ceiling becomes an overrun on the run that crosses it.
        passed, reasons = usage_probe.gate(self.reading(80, 2, 1), {"rolling": 80})
        self.assertFalse(passed)
        self.assertIn("rolling: 80% has reached the 80% line", reasons)

    def test_under_the_line_passes(self) -> None:
        passed, reasons = usage_probe.gate(self.reading(79, 2, 1), {"rolling": 80})
        self.assertTrue(passed)
        self.assertEqual([], reasons)

    def test_only_declared_windows_are_gated(self) -> None:
        # An undeclared limit is not a limit of zero. Enforcing one would stop
        # runs for a rule nobody stated.
        passed, _ = usage_probe.gate(self.reading(0, 99, 99), {"rolling": 80})
        self.assertTrue(passed)

    def test_a_missing_window_is_a_reason_not_a_silent_pass(self) -> None:
        reading = self.reading(0, 2, 1)
        del reading["windows"]["rolling"]
        passed, reasons = usage_probe.gate(reading, {"rolling": 80})
        self.assertFalse(passed)
        self.assertIn("rolling: no reading, cannot be gated", reasons)

    def test_an_unreadable_counter_is_unknown_and_never_permission(self) -> None:
        def explode() -> dict:
            raise usage_probe.ProbeError("usage endpoint unreachable: boom")
        with self.assertRaises(usage_probe.ProbeError):
            usage_probe.snapshot(reader=explode)

    def test_a_malformed_percent_is_refused_rather_than_coerced(self) -> None:
        with self.assertRaises(usage_probe.ProbeError):
            usage_probe.snapshot(
                reader=lambda: {"rolling": {"percent": "0"}, "weekly": {},
                                "monthly": {}}
            )

    def test_delta_reports_points_consumed(self) -> None:
        before = self.reading(0, 2, 1)
        after = self.reading(7, 3, 1)
        points = usage_probe.delta(before, after)
        self.assertEqual(7, points["rolling"]["points"])
        self.assertEqual(1, points["weekly"]["points"])
        self.assertEqual(0, points["monthly"]["points"])

    def test_a_window_that_reset_mid_run_is_not_reported_as_negative_use(self) -> None:
        # Usage going down is not something a run can do. Reporting -2 points
        # would invite a reader to average it into a cost estimate.
        before = self.reading(0, 90, 1, resets="T1")
        after = self.reading(0, 1, 1, resets="T2")
        points = usage_probe.delta(before, after)
        self.assertIsNone(points["weekly"]["points"])
        self.assertIn("reset between readings", points["weekly"]["note"])


class ApparatusBaselineTests(unittest.TestCase):
    """The control for the hazard `compare.py` structurally cannot see.

    `compare.py` checks that the subjects agree with EACH OTHER. One machine,
    one `pip install -U`, five runs -- and all five agree perfectly while every
    one of them was measured by a primitive nobody declared. Only a baseline
    written when the tree was cut can catch that, so this is the check that has
    to hold.
    """

    def setUp(self) -> None:
        self.baseline = adapters.HERE / "apparatus.json"
        self.existed = self.baseline.exists()
        self.original = self.baseline.read_bytes() if self.existed else None

    def tearDown(self) -> None:
        if self.original is not None:
            self.baseline.write_bytes(self.original)
        elif self.baseline.exists():
            self.baseline.unlink()

    def test_an_unmaterialized_tree_says_so_rather_than_going_quiet(self) -> None:
        if self.baseline.exists():
            self.baseline.unlink()
        baseline = adapters._apparatus()["baseline"]
        self.assertFalse(baseline["present"])
        self.assertIsNone(baseline["agrees"])
        self.assertIn("not materialized", baseline["note"])

    def test_a_matching_baseline_agrees(self) -> None:
        live = adapters._apparatus()
        self.baseline.write_text(
            json.dumps({"version": live["version"], "modules": live["modules"]}),
            encoding="utf-8",
        )
        self.assertTrue(adapters._apparatus()["baseline"]["agrees"])

    def test_an_upgraded_primitive_is_caught_and_named(self) -> None:
        live = adapters._apparatus()
        drifted = json.loads(json.dumps(live["modules"]))
        drifted["capture"]["sha256"] = "0" * 64
        self.baseline.write_text(
            json.dumps({"version": "0.0.1-older", "modules": drifted}),
            encoding="utf-8",
        )
        baseline = adapters._apparatus()["baseline"]
        self.assertFalse(baseline["agrees"])
        self.assertEqual(["capture"], baseline["changed_modules"])
        self.assertEqual("0.0.1-older", baseline["version"])


class ExitStatusTests(unittest.TestCase):
    """The status follows `hwb`'s own convention, one level down.

    `cli.py`: "a harness that worked exits 0, whatever the steps did." An
    adapter is a harness over a subject, so the subject's own success cannot
    reach this number -- it is recorded data, per the README.
    """

    def test_a_declined_task_is_still_a_valid_measurement(self) -> None:
        # The inversion that matters. Before the split this returned 1, and
        # `retry` -- which can only see `exit == 0` -- re-ran a harness that had
        # captured perfectly and simply declined the task, at full gateway cost.
        self.assertEqual(subject_runner.exit_status(True, False), 0)

    def test_a_broken_measurement_is_one(self) -> None:
        self.assertEqual(subject_runner.exit_status(False, False), 1)

    def test_an_interrupted_run_refuses_rather_than_failing(self) -> None:
        # 3 is `hwb diff`'s refusal code: "a script must never be able to read
        # a refusal as a difference." An operator's Ctrl-C is not a verdict
        # about a harness, and must not be readable as one -- in either
        # direction, which is why it outranks a passing adapter verdict too.
        self.assertEqual(subject_runner.exit_status(True, True), 3)
        self.assertEqual(subject_runner.exit_status(False, True), 3)

    def test_the_status_set_is_exactly_the_workbench_convention(self) -> None:
        produced = {
            subject_runner.exit_status(adapter, interrupted)
            for adapter in (True, False)
            for interrupted in (True, False)
        }
        self.assertEqual(produced, {0, 1, 3})


class ContractComparisonTests(unittest.TestCase):
    @staticmethod
    def stream() -> dict:
        return {
            "bytes": 0,
            "source_bytes": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
            "base64": "",
            "text": "",
            "redaction_count": 0,
        }

    def outer(self, subject: str, *, passed: bool) -> dict:
        empty_sha = hashlib.sha256(b"").hexdigest()
        capture = {
            name: self.stream() for name in ("stdout", "stderr", "sidecar")
        }
        capture.update({
            "limits": {
                "stdout_bytes": 1024,
                "stderr_bytes": 1024,
                "sidecar_bytes": 1024,
            },
            "overflow": {
                "stdout": False,
                "stderr": False,
                "sidecar": False,
            },
            "returncode": 0 if passed else 124,
            "termination_reason": None if passed else "timeout",
            "timed_out": not passed,
        })
        adapter = {
            "schema": "cross-harness-adapter-run/v0.1",
            "subject": {"name": subject},
            "request": {
                "prompt_sha256": "prompt",
                "input_digests": {"task.md": "digest"},
            },
            "apparatus": {
                "package": "harness_workbench",
                "version": "0.0.0-test",
                "modules": {
                    "canon": {"file": "canon.py", "sha256": empty_sha},
                    "capture": {"file": "capture.py", "sha256": empty_sha},
                },
            },
            "capabilities": {},
            "invocation": {},
            "isolation": {},
            "capture": capture,
            "lifecycle": {
                "acquisition": "native_jsonl",
                "completeness": "native_terminal_event",
                "tool_executions": [],
            },
            "workspace": {},
            "verdict": {"passed": passed},
            "outcome": {"passed": passed, "expected_sha256": "effect"},
        }
        return {
            "schema": "cross-harness-experiment-run/v0.1",
            "subject": subject,
            "verdict": {"passed": passed},
            "adapter": adapter,
        }

    def test_contract_can_pass_when_one_subject_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for subject in sorted(comparator.SUBJECTS):
                path = Path(directory) / f"{subject}.json"
                path.write_text(
                    json.dumps(self.outer(subject, passed=subject != "hermes")),
                    encoding="utf-8",
                )
                paths.append(path)
            result = comparator.compare(paths)
        self.assertTrue(result["contract_passed"])
        self.assertTrue(result["subjects"]["hermes"]["timed_out"])
        self.assertFalse(result["subjects"]["hermes"]["outcome_passed"])

    def test_refused_sidecar_is_reported_as_a_refusal_not_as_corruption(
        self,
    ) -> None:
        # capture_file stores nothing for evidence it refuses, so the envelope
        # carries `base64: None` and the reason in `errors`. The comparator
        # used to decode the None, catch the TypeError, and report "not valid
        # base64" -- naming the symptom and hiding the cause.
        errors: list[str] = []
        comparator.verify_capture(
            "deepseek",
            {
                "stdout": self.stream(), "stderr": self.stream(),
                "sidecar": dict(
                    self.stream(),
                    base64=None,
                    text=None,
                    exists=True,
                    errors=["evidence exceeds 524288-byte capture limit: 900000 bytes"],
                ),
                "limits": {
                    "stdout_bytes": 1024, "stderr_bytes": 1024,
                    "sidecar_bytes": 524288,
                },
                "overflow": {"stdout": False, "stderr": False, "sidecar": True},
                "returncode": 0,
                "termination_reason": None,
                "timed_out": False,
            },
            errors,
        )
        self.assertIn(
            "deepseek sidecar: evidence exceeds 524288-byte capture limit:"
            " 900000 bytes",
            errors,
        )
        self.assertNotIn("deepseek sidecar is not valid base64", errors)

    def test_missing_required_sidecar_is_visible_to_the_comparator(self) -> None:
        # The harder half: a required sidecar that was never created stores
        # empty bytes, and empty bytes digest perfectly well. Every structural
        # check passed and the comparator reported nothing at all.
        errors: list[str] = []
        comparator.verify_capture(
            "hermes",
            {
                "stdout": self.stream(), "stderr": self.stream(),
                "sidecar": dict(
                    self.stream(),
                    exists=False,
                    errors=["required evidence file was not created"],
                ),
                "limits": {
                    "stdout_bytes": 1024, "stderr_bytes": 1024,
                    "sidecar_bytes": 1024,
                },
                "overflow": {"stdout": False, "stderr": False, "sidecar": False},
                "returncode": 0,
                "termination_reason": None,
                "timed_out": False,
            },
            errors,
        )
        self.assertIn(
            "hermes sidecar: required evidence file was not created", errors
        )

    def test_contract_rejects_mixed_capture_apparatus(self) -> None:
        # The inversion of the apparatus check. Every other input to a run is
        # bound by the spec and would be caught by freeze; the capture
        # primitive is imported from the installed package and cannot be, so
        # this comparison is the only place a mismatch can surface at all.
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for subject in sorted(comparator.SUBJECTS):
                outer = self.outer(subject, passed=True)
                if subject == "deepseek":
                    outer["adapter"]["apparatus"]["modules"]["canon"] = {
                        "file": "canon.py", "sha256": "other",
                    }
                path = Path(directory) / f"{subject}.json"
                path.write_text(json.dumps(outer), encoding="utf-8")
                paths.append(path)
            result = comparator.compare(paths)
        self.assertFalse(result["contract_passed"])
        self.assertIn(
            "subjects were not captured by the same apparatus", result["errors"]
        )

    def sampled_run_dir(self, root: Path, subject: str, draws: list[dict]) -> Path:
        """A run store shaped the way `sample` leaves one: N numbered attempts."""
        path = root / subject
        (path / "steps" / "s" / "attempts").mkdir(parents=True)
        # freeze and receipt must agree with what the adapter reported, or
        # verify_record fails every draw for a reason unrelated to sampling.
        bound = {
            name: "sha256:" + digest
            for name, digest in draws[0]["adapter"]["request"][
                "input_digests"
            ].items()
        }
        (path / "record.json").write_text(
            json.dumps({
                "steps": [{"id": "s"}],
                "extras": {
                    "freeze": {"digests": bound, "drifted": False},
                    "receipt": {"bound": {"inputs": bound}},
                },
            }),
            encoding="utf-8",
        )
        for index, outer in enumerate(draws):
            attempt = path / "steps" / "s" / "attempts" / str(index)
            attempt.mkdir()
            (attempt / "stdout.bin").write_text(
                json.dumps(outer), encoding="utf-8"
            )
        return path

    def test_every_draw_of_a_sampled_subject_is_read(self) -> None:
        # The inversion. `load_source` read attempts/0 and stopped, so a
        # subject whose second draw violated the contract compared clean --
        # which is the exact failure `sample` exists to prevent.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for subject in sorted(comparator.SUBJECTS):
                good = self.outer(subject, passed=True)
                draws = [good, json.loads(json.dumps(good))]
                if subject == "codex":
                    draws[1]["adapter"]["capture"]["stdout"]["sha256"] = "forged"
                paths.append(self.sampled_run_dir(root, subject, draws))
            result = comparator.compare(paths)
        self.assertFalse(result["contract_passed"])
        self.assertIn("codex draw 1 stdout digest disagrees", result["errors"])
        self.assertEqual(result["subjects"]["codex"]["draws"], 2)

    def test_draw_counts_are_reported_rather_than_reduced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for subject in sorted(comparator.SUBJECTS):
                draws = [
                    self.outer(subject, passed=True),
                    self.outer(subject, passed=subject != "hermes"),
                    self.outer(subject, passed=True),
                ]
                paths.append(self.sampled_run_dir(root, subject, draws))
            result = comparator.compare(paths)
        # The contract holds on every draw; what the model did varied. Those
        # are different questions and only the second one has a count.
        self.assertTrue(result["contract_passed"])
        self.assertEqual(result["subjects"]["hermes"]["draws"], 3)
        self.assertEqual(result["subjects"]["hermes"]["outcome_passed"], 2)
        self.assertEqual(result["subjects"]["claude"]["outcome_passed"], 3)

    def test_contract_rejects_forged_raw_capture_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for subject in sorted(comparator.SUBJECTS):
                outer = self.outer(subject, passed=True)
                if subject == "codex":
                    outer["adapter"]["capture"]["stdout"]["sha256"] = "forged"
                path = Path(directory) / f"{subject}.json"
                path.write_text(json.dumps(outer), encoding="utf-8")
                paths.append(path)
            result = comparator.compare(paths)
        self.assertFalse(result["contract_passed"])
        self.assertIn("codex stdout digest disagrees", result["errors"])


if __name__ == "__main__":
    unittest.main()
