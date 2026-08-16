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
from common import (
    EXPECTED_CONTENT,
    ProcessResult,
    capture_bytes,
    credential_values,
    manifest,
    outcome,
    parse_jsonl,
    redact_bytes,
    repair_outcome,
    run_bounded,
)


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
        events, errors = parse_jsonl(b'{"ok":true}\n[]\nnot-json\n')
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
        for subject in ("claude", "codex", "deepseek", "hermes"):
            spec = json.loads(
                (adapters.HERE / f"{subject}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(tuple(spec["steps"][0]["inputs"]), adapters.INPUTS)
            self.assertEqual(
                [feature["name"] for feature in spec["features"]],
                ["freeze", "receipt"],
            )

    def test_repair_specs_bind_the_same_complete_input_set(self) -> None:
        for subject in ("claude", "codex", "deepseek", "hermes"):
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
        self.assertEqual(result.returncode, 125)
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
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.termination_reason, "timeout")
        self.assertEqual(result.stdout, b"started\n")

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
        self.assertEqual(result.termination_reason, "timeout")
        self.assertLess(elapsed, 0.3)

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
    def process(returncode: int) -> ProcessResult:
        return ProcessResult(
            args=["python3.11", "-m", "unittest", "-v"],
            returncode=returncode,
            stdout=b"",
            stderr=b"",
            stdout_source_bytes=0,
            stderr_source_bytes=0,
            termination_reason=None,
            stdout_overflow=False,
            stderr_overflow=False,
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


class ContractComparisonTests(unittest.TestCase):
    def outer(self, subject: str, *, passed: bool) -> dict:
        empty_sha = hashlib.sha256(b"").hexdigest()
        capture = {
            name: {
                "bytes": 0,
                "source_bytes": 0,
                "sha256": empty_sha,
                "base64": "",
                "text": "",
                "redaction_count": 0,
            }
            for name in ("stdout", "stderr", "sidecar")
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
