#!/usr/bin/env python3
"""Plan or execute a guarded provider-route canary for the shared gateway."""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
_CHECKOUT_SOURCE = HERE.parents[1]
if (_CHECKOUT_SOURCE / "harness_workbench" / "__init__.py").is_file():
    sys.path.insert(0, str(_CHECKOUT_SOURCE.resolve()))

import adapters
import certify
import doctor
from harness_workbench.canon import digest_file
from harness_workbench.capture import credential_values, redact_bytes
import preflight
import usage_probe


SCHEMA = "cross-harness-provider-route-canary/v0.1"
SUBJECTS = ("deepseek", "hermes", "pi")
WORKLOAD = "repair"
NOMINAL_CALLS = len(SUBJECTS)
MAXIMUM_CALLS = NOMINAL_CALLS
DEFAULT_LIMITS = {"rolling": 80, "weekly": 90}
FAKE_KEY = "route-canary-fake-provider-token"
FAKE_KEY_ENV = "HWB_ROUTE_CANARY_FAKE_KEY"
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RESPONSE_LINES = 256
RENDER_TIMEOUT_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = {"deepseek": 120, "hermes": 180, "pi": 120}
GITLEAKS_CONFIG = (HERE / ".gitleaks.toml").resolve()


class CanaryError(RuntimeError):
    """The route canary cannot make a positive, bounded claim."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class _CaptureServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], expected_model: str):
        super().__init__(address, _CaptureHandler)
        self.expected_model = expected_model
        self.tool_requests: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.lock = threading.Lock()


class _CaptureHandler(BaseHTTPRequestHandler):
    server: _CaptureServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        self._json_response(200, {})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json_response(400, {"error": "invalid content length"})
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json_response(413, {"error": "request body outside bound"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_response(400, {"error": "request body is not JSON"})
            return
        if not isinstance(payload, dict):
            self._json_response(400, {"error": "request JSON is not an object"})
            return
        tools = payload.get("tools")
        if not isinstance(tools, list) or not tools:
            # Hermes may create a session title before its actual agent turn.
            model = str(payload.get("model", self.server.expected_model))
            self._json_response(200, {
                "id": "route-canary-auxiliary",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "route canary"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2},
            })
            return

        issues: list[str] = []
        if self.path != "/v1/chat/completions":
            issues.append(f"unexpected request path: {self.path}")
        if self.headers.get("Authorization") != f"Bearer {FAKE_KEY}":
            issues.append("loopback request did not use the fake bearer credential")
        if payload.get("model") != self.server.expected_model:
            issues.append("rendered request did not use the selected model")
        if payload.get("stream") is not True:
            issues.append("rendered tool request is not streaming")
        safe_headers = {
            name.lower(): value
            for name, value in self.headers.items()
            if name.lower() not in {
                "authorization", "cookie", "proxy-authorization", "host",
                "content-length", "connection",
            }
            and not any(
                marker in name.lower() for marker in ("key", "secret", "token")
            )
        }
        with self.server.lock:
            self.server.errors.extend(issues)
            self.server.tool_requests.append({
                "path": self.path,
                "headers": safe_headers,
                "body": raw,
                "body_json": payload,
            })
        # The renderer must terminate without receiving a model response.
        self._json_response(403, {"error": {"message": "route canary capture stop"}})


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _active_gateway() -> tuple[str, dict[str, Any]]:
    name, profile = adapters._active_profile()
    if profile.get("kind") != "gateway":
        raise ValueError("provider-route canary requires an active gateway profile")
    missing = [subject for subject in SUBJECTS if subject not in profile.get("models", {})]
    if missing:
        raise ValueError("gateway profile is missing canary subjects: " + ", ".join(missing))
    return name, profile


def _validate_limits(limits: dict[str, int]) -> None:
    certify._validate_limits(limits)


def _scanner(*, required: bool) -> tuple[Path | None, str | None]:
    executable = shutil.which("gitleaks")
    if not executable:
        if required:
            raise ValueError("gitleaks is required before a live provider-route canary")
        return None, None
    path = Path(executable).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("resolved gitleaks executable is not usable")
    return path, digest_file(path)


def build_plan(
    limits: dict[str, int], *, require_live_prerequisites: bool = False
) -> dict[str, Any]:
    """Describe the three-route canary while authorizing zero model calls."""
    _validate_limits(limits)
    profile_name, profile = _active_gateway()
    scanner, scanner_digest = _scanner(required=require_live_prerequisites)
    if not GITLEAKS_CONFIG.is_file():
        raise ValueError("the bundled gitleaks configuration is missing")
    python = Path(sys.executable).resolve()
    source_root = certify.SOURCE_ROOT
    return {
        "schema": SCHEMA,
        "live": False,
        "model_calls_authorized": 0,
        "nominal_model_calls": NOMINAL_CALLS,
        "maximum_model_calls": MAXIMUM_CALLS,
        "subjects": list(SUBJECTS),
        "workload": WORKLOAD,
        "usage_limits": dict(sorted(limits.items())),
        "record_dir_required_for_live": True,
        "stop_policy": "stop before the matrix on the first non-positive route result",
        "profile": {
            "name": profile_name,
            "kind": "gateway",
            "base_url": str(profile["base_url"]),
            "models": {subject: str(profile["models"][subject]) for subject in SUBJECTS},
            "identity_strength": str(profile["identity_strength"]),
        },
        "apparatus": {
            "python": str(python),
            "python_sha256": digest_file(python),
            "source_root": str(source_root.resolve()),
            "subjects_root": str(HERE),
            "gitleaks": str(scanner) if scanner is not None else None,
            "gitleaks_sha256": scanner_digest,
            "gitleaks_available": scanner is not None,
            "gitleaks_config": str(GITLEAKS_CONFIG),
            "gitleaks_config_sha256": digest_file(GITLEAKS_CONFIG),
            "modules": {
                Path(path).name: digest_file(path)
                for path in (
                    __file__, adapters.__file__, doctor.__file__, preflight.__file__,
                    usage_probe.__file__, certify.__file__,
                )
            },
        },
        "request_bounds": {
            "render_timeout_seconds": RENDER_TIMEOUT_SECONDS,
            "network_timeout_seconds": dict(REQUEST_TIMEOUT_SECONDS),
            "request_bytes": MAX_REQUEST_BYTES,
            "response_bytes": MAX_RESPONSE_BYTES,
            "response_lines": MAX_RESPONSE_LINES,
            "redirects_followed": False,
        },
        "stages": [
            "offline_prerequisites",
            "fresh_usage_gate",
            "render_exact_tool_requests_to_loopback",
            "replay_exact_request_bodies_until_first_stream_event",
            "post_run_usage_and_postflight",
            "gitleaks_and_exact_credential_scan",
        ],
    }


def _render_request(subject: str, profile: dict[str, Any]) -> dict[str, Any]:
    model = str(profile["models"][subject])
    server = _CaptureServer(("127.0.0.1", 0), model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    fake_profile = {
        "kind": "gateway",
        "base_url": f"http://127.0.0.1:{port}/v1",
        "api_key_env": FAKE_KEY_ENV,
        "api_key_placeholder": FAKE_KEY,
        "identity_strength": "gateway_model_label",
        "verify_digest": False,
        "models": dict(profile["models"]),
        "subject_key_env": {
            "deepseek": FAKE_KEY_ENV,
            "hermes": "OPENAI_API_KEY",
            "pi": FAKE_KEY_ENV,
        },
    }
    original_active = adapters._active_profile
    capture_record: dict[str, Any] | None = None
    capture_error: str | None = None
    try:
        adapters._active_profile = lambda: ("route-canary-loopback", fake_profile)
        try:
            capture_record = adapters.capture(
                subject,
                WORKLOAD,
                timeout=RENDER_TIMEOUT_SECONDS,
                stdout_limit=certify.MAX_CAPTURE_BYTES,
                stderr_limit=certify.MAX_CAPTURE_BYTES,
            )
        except (OSError, TypeError, ValueError, adapters.AdapterError) as error:
            capture_error = str(error)
    finally:
        adapters._active_profile = original_active
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if thread.is_alive():
        raise CanaryError(f"{subject} loopback capture server did not stop")
    if not server.tool_requests:
        raise CanaryError(
            f"{subject} did not render a tool-bearing chat-completions request"
            + (f": {capture_error}" if capture_error else "")
        )
    if len(server.tool_requests) != 1:
        raise CanaryError(f"{subject} rendered more than one tool-bearing request")
    if server.errors:
        raise CanaryError(f"{subject} rendered an invalid route request: " + "; ".join(server.errors))
    request = server.tool_requests[0]
    return {
        **request,
        "capture_record": capture_record,
        "capture_error": capture_error,
        "cleanup": {
            "server_thread_stopped": True,
            "adapter_process_group_clean": (
                capture_record.get("capture", {}).get("process_group", {}).get(
                    "alive_after_cleanup"
                ) is False
                if isinstance(capture_record, dict)
                else None
            ),
        },
    }


def _response_bytes(response: Any) -> tuple[bytes, dict[str, Any] | None, str | None]:
    chunks: list[bytes] = []
    total = 0
    event: dict[str, Any] | None = None
    error: str | None = None
    for _ in range(MAX_RESPONSE_LINES):
        remaining = MAX_RESPONSE_BYTES - total
        if remaining <= 0:
            error = "response exceeded the retained byte bound"
            break
        line = response.readline(remaining + 1)
        if not line:
            break
        chunks.append(line[:remaining])
        total += min(len(line), remaining)
        if len(line) > remaining:
            error = "response exceeded the retained byte bound"
            break
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            continue
        data = stripped[5:].strip()
        if data == b"[DONE]":
            error = "stream ended before a JSON event"
            break
        try:
            parsed = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            error = "first stream data event was not JSON"
            break
        if not isinstance(parsed, dict):
            error = "first stream data event was not an object"
            break
        event = parsed
        break
    else:
        error = "response exceeded the retained line bound"
    if event is None and error is None:
        error = "response ended before a JSON stream event"
    return b"".join(chunks), event, error


def _replay_request(
    profile: dict[str, Any],
    request: dict[str, Any],
    credential: str,
    *,
    timeout: float,
) -> tuple[dict[str, Any], bytes]:
    url = str(profile["base_url"]).rstrip("/") + "/chat/completions"
    headers = {
        name: str(value)
        for name, value in request["headers"].items()
        if name in {"accept", "content-type", "user-agent"}
    }
    headers["Authorization"] = f"Bearer {credential}"
    headers.setdefault("Content-Type", "application/json")
    outbound = urllib.request.Request(
        url,
        data=request["body"],
        headers=headers,
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    started = time.monotonic()
    status: int | None = None
    response_headers: dict[str, str] = {}
    event: dict[str, Any] | None = None
    error: str | None = None
    raw = b""
    try:
        with opener.open(outbound, timeout=timeout) as response:
            status = int(response.status)
            response_headers = {
                name.lower(): value
                for name, value in response.headers.items()
                if name.lower() in {"content-type", "content-encoding"}
            }
            raw, event, error = _response_bytes(response)
    except urllib.error.HTTPError as failure:
        status = int(failure.code)
        response_headers = {
            name.lower(): value
            for name, value in failure.headers.items()
            if name.lower() in {"content-type", "content-encoding"}
        }
        raw = failure.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raw = raw[:MAX_RESPONSE_BYTES]
            error = "error response exceeded the retained byte bound"
        else:
            error = f"gateway returned HTTP {status}"
    except Exception as failure:  # DNS, TLS, socket timeout, disconnect
        error = f"gateway request failed: {failure}"
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if status is not None and not 200 <= status < 300 and error is None:
        error = f"gateway returned HTTP {status}"
    if isinstance(event, dict) and event.get("error") is not None:
        error = "first stream event contains an error"
    if isinstance(event, dict) and not isinstance(event.get("choices"), list):
        error = "first stream event is not an OpenAI chat-completion chunk"
    receipt = {
        "url": url,
        "method": "POST",
        "request_sha256": _sha256(request["body"]),
        "request_bytes": len(request["body"]),
        "status": status,
        "response_headers": response_headers,
        "response_sha256": _sha256(raw),
        "response_bytes": len(raw),
        "first_event_sha256": (
            _sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode())
            if event is not None else None
        ),
        "elapsed_ms": elapsed_ms,
        "timeout_seconds": timeout,
        "passed": status is not None and 200 <= status < 300 and event is not None
                  and error is None,
        "error": error,
        "connection_closed_after_first_event": event is not None,
        "redirects_followed": False,
    }
    return receipt, raw


def _limits(overrides: list[str]) -> dict[str, int]:
    limits = dict(DEFAULT_LIMITS)
    limits.update(usage_probe._limits(overrides))
    return limits


def execute(
    plan: dict[str, Any],
    record_dir: Path,
    *,
    config_path: Path,
    credential_file: Path | None = None,
    hermes_root: Path | None = None,
) -> tuple[dict[str, Any], int]:
    scanner = plan.get("apparatus", {}).get("gitleaks")
    scanner_digest = plan.get("apparatus", {}).get("gitleaks_sha256")
    if not isinstance(scanner, str) or not isinstance(scanner_digest, str):
        raise ValueError("gitleaks is required before a live provider-route canary")
    scanner_path = Path(scanner)
    if (
        not scanner_path.is_absolute()
        or not scanner_path.is_file()
        or not os.access(scanner_path, os.X_OK)
        or digest_file(scanner_path) != scanner_digest
    ):
        raise ValueError("the planned gitleaks executable is no longer usable")
    destination = record_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    requests_root = destination / "requests"
    responses_root = destination / "responses"
    captures_root = destination / "captures"
    process_root = destination / "process"
    for path in (requests_root, responses_root, captures_root, process_root):
        path.mkdir(mode=0o700)
    report: dict[str, Any] = {
        **plan,
        "live": True,
        "model_calls_authorized": MAXIMUM_CALLS,
        "model_calls_started": 0,
        "record_dir": str(destination),
        "routes": {},
        "processes": [],
    }

    try:
        local = preflight.prepare_environment(
            SUBJECTS,
            config_path=config_path,
            credential_file=credential_file,
            hermes_root=hermes_root,
        )
        initial_doctor = doctor.report(SUBJECTS)
    except (OSError, TypeError, ValueError, preflight.PreflightError) as error:
        report.update({"status": "preflight_error", "passed": False,
                       "error": f"offline prerequisites: {error}"})
        certify._write_json_exclusive(destination / "route-canary-report.json", report)
        return report, 2
    report["preflight"] = {**initial_doctor, "local": local}
    if initial_doctor.get("overall_status") != "ready":
        report.update({"status": "preflight_blocked", "passed": False})
        certify._write_json_exclusive(destination / "route-canary-report.json", report)
        return report, 1

    profile_name, profile = _active_gateway()
    if profile_name != plan["profile"]["name"] or {
        subject: str(profile["models"][subject]) for subject in SUBJECTS
    } != plan["profile"]["models"]:
        raise ValueError("the active gateway profile changed after planning")
    credential_name = str(profile["api_key_env"])
    credential = os.environ.get(credential_name)
    if not credential:
        raise ValueError(f"{credential_name} is unavailable after preflight")
    values = tuple(sorted(set(credential_values(os.environ)) | {FAKE_KEY}, key=len,
                          reverse=True))
    try:
        usage_before = usage_probe.snapshot()
        certify._write_json_exclusive(destination / "usage-before.json", usage_before)
    except (OSError, usage_probe.ProbeError) as error:
        report.update({"status": "usage_unknown", "passed": False,
                       "error": f"usage before run: {error}"})
        certify._write_json_exclusive(destination / "route-canary-report.json", report)
        return report, 3
    gate_passed, gate_reasons = usage_probe.gate(usage_before, plan["usage_limits"])
    report["usage"] = {
        "before": usage_before,
        "gate": {"limits": plan["usage_limits"], "passed": gate_passed,
                 "reasons": gate_reasons},
    }
    if not gate_passed:
        report.update({"status": "usage_gate_blocked", "passed": False})
        certify._write_json_exclusive(destination / "route-canary-report.json", report)
        return report, 1

    print(json.dumps({
        "stage": "provider_route_canary_live_authorization",
        "nominal_model_calls": NOMINAL_CALLS,
        "maximum_model_calls": MAXIMUM_CALLS,
        "usage": usage_before,
        "stop_thresholds": plan["usage_limits"],
        "record_dir": str(destination),
        "routes": plan["profile"],
    }, sort_keys=True), flush=True)

    operational_errors: list[str] = []
    for subject in SUBJECTS:
        try:
            rendered = _render_request(subject, profile)
        except (OSError, TypeError, ValueError, CanaryError) as error:
            operational_errors.append(f"{subject} request render failed: {error}")
            break
        body = rendered["body"]
        request_path = requests_root / f"{subject}.json"
        capture_path = captures_root / f"{subject}.json"
        certify._write_bytes_exclusive(request_path, body)
        capture_document = {
            "subject": subject,
            "capture_record": rendered["capture_record"],
            "capture_error": rendered["capture_error"],
            "cleanup": rendered["cleanup"],
        }
        certify._write_json_exclusive(capture_path, capture_document)
        route = {
            "subject": subject,
            "model": str(profile["models"][subject]),
            "request": str(request_path.relative_to(destination)),
            "request_sha256": digest_file(request_path),
            "request_bytes": len(body),
            "request_headers": rendered["headers"],
            "capture": str(capture_path.relative_to(destination)),
            "capture_sha256": digest_file(capture_path),
            "render_cleanup": rendered["cleanup"],
        }
        report["routes"][subject] = route
        report["model_calls_started"] += 1
        receipt, raw_response = _replay_request(
            profile,
            rendered,
            credential,
            timeout=REQUEST_TIMEOUT_SECONDS[subject],
        )
        redacted_response, redactions = redact_bytes(raw_response, values)
        response_path = responses_root / f"{subject}.bin"
        certify._write_bytes_exclusive(response_path, redacted_response)
        route["response"] = str(response_path.relative_to(destination))
        route["response_sha256"] = digest_file(response_path)
        route["response_redactions"] = redactions
        route["receipt"] = receipt
        if not receipt["passed"]:
            operational_errors.append(
                f"{subject} provider route did not return a valid first stream event"
            )
            break

    after_error: str | None = None
    try:
        usage_after = usage_probe.snapshot()
        certify._write_json_exclusive(destination / "usage-after.json", usage_after)
    except (OSError, usage_probe.ProbeError) as error:
        after_error = str(error)
        report["usage"]["after_error"] = after_error
    else:
        report["usage"]["after"] = usage_after
        report["usage"]["delta"] = usage_probe.delta(usage_before, usage_after)
        post_gate_passed, post_gate_reasons = usage_probe.gate(
            usage_after, plan["usage_limits"]
        )
        report["usage"]["post_gate"] = {
            "limits": plan["usage_limits"],
            "passed": post_gate_passed,
            "reasons": post_gate_reasons,
        }
    try:
        postflight = doctor.report(SUBJECTS)
    except (OSError, TypeError, ValueError) as error:
        postflight = {"overall_status": "error", "error": str(error)}
    report["postflight"] = postflight

    gitleaks_report = destination / "gitleaks-report.json"
    result, receipt = certify._run_command(
        process_root,
        0,
        "gitleaks-route-canary-evidence",
        [
            scanner,
            "dir",
            "--no-banner",
            "--redact=100",
            "--max-target-megabytes",
            str(certify.MAX_SCAN_FILE_BYTES // (1024 * 1024)),
            "--timeout",
            "120",
            "--config",
            plan["apparatus"]["gitleaks_config"],
            "--report-format",
            "json",
            "--report-path",
            str(gitleaks_report),
            str(destination),
        ],
        cwd=HERE,
        env=certify._child_environment(),
        timeout=150,
        stdout_limit=1024 * 1024,
        stderr_limit=1024 * 1024,
    )
    report["processes"].append(receipt)
    gitleaks_clean = certify._process_clean(result) and result.returncode == 0
    if not gitleaks_report.exists():
        certify._write_json_exclusive(gitleaks_report, {"findings": "report missing"})
        gitleaks_clean = False
    report["gitleaks"] = {
        "passed": gitleaks_clean,
        "report": str(gitleaks_report.relative_to(destination)),
        "report_sha256": digest_file(gitleaks_report),
    }
    if not gitleaks_clean:
        operational_errors.append("gitleaks did not prove retained canary evidence clean")
    if after_error is not None:
        operational_errors.append("post-run usage could not be retained")
    elif report["usage"]["post_gate"]["passed"] is not True:
        operational_errors.append(
            "post-canary usage reached a stop threshold; the matrix must not start"
        )
    if postflight.get("overall_status") != "ready":
        operational_errors.append("offline postflight is not ready")
    if report["model_calls_started"] > MAXIMUM_CALLS:
        operational_errors.append("canary exceeded its authorized call maximum")

    report["cleanup"] = {
        "all_process_groups_clean": all(
            process["cleanup_passed"] for process in report["processes"]
        ),
        "all_renderers_clean": all(
            route.get("render_cleanup", {}).get("server_thread_stopped") is True
            and route.get("render_cleanup", {}).get("adapter_process_group_clean")
            in {True, None}
            for route in report["routes"].values()
        ),
    }
    report["status"] = "passed" if not operational_errors else "operational_failure"
    report["passed"] = not operational_errors
    report["errors"] = operational_errors
    report_path = destination / "route-canary-report.json"
    report_bytes = certify._json_bytes(report)
    scan = certify._scan_retained(
        destination,
        values,
        virtual_files={report_path.name: report_bytes},
    )
    if not scan["passed"]:
        report["status"] = "credential_scan_failed"
        report["passed"] = False
        report["errors"].append(
            "exact credential-value scan did not prove every retained file clean"
        )
        report_bytes = certify._json_bytes(report)
        scan = certify._scan_retained(
            destination,
            values,
            virtual_files={report_path.name: report_bytes},
        )
    scan_path = destination / "credential-scan.json"
    scan["files"] = sorted(set(scan["files"] + [scan_path.name]))
    report["security"] = {
        "gitleaks_passed": gitleaks_clean,
        "credential_scan": scan_path.name,
        "credential_scan_passed": scan["passed"],
    }
    # Security is included in the final report, so scan that exact representation.
    report_bytes = certify._json_bytes(report)
    final_scan = certify._scan_retained(
        destination,
        values,
        virtual_files={report_path.name: report_bytes},
    )
    final_scan["files"] = sorted(set(final_scan["files"] + [scan_path.name]))
    if not final_scan["passed"]:
        report["status"] = "credential_scan_failed"
        report["passed"] = False
        report["errors"].append("final report failed exact credential-value scan")
        report_bytes = certify._json_bytes(report)
        final_scan = certify._scan_retained(
            destination, values, virtual_files={report_path.name: report_bytes}
        )
        final_scan["files"] = sorted(set(final_scan["files"] + [scan_path.name]))
    certify._write_bytes_exclusive(report_path, report_bytes)
    certify._write_json_exclusive(scan_path, final_scan)
    if operational_errors:
        return report, 2
    return report, 0 if report["passed"] and final_scan["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--live",
        action="store_true",
        help="execute three bounded route calls; without this flag no credential or model is used",
    )
    parser.add_argument(
        "--record-dir",
        type=Path,
        help="required with --live; the directory must not already exist",
    )
    parser.add_argument(
        "--max",
        action="append",
        default=[],
        metavar="WINDOW=PCT",
        help="override a default usage gate (rolling=80, weekly=90)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("HWB_PREFLIGHT_CONFIG", preflight.DEFAULT_CONFIG)),
    )
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--hermes-root", type=Path)
    args = parser.parse_args()
    try:
        plan = build_plan(
            _limits(args.max), require_live_prerequisites=args.live
        )
        if not args.live:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        if args.record_dir is None:
            raise ValueError("--record-dir is required with --live")
        report, status = execute(
            plan,
            args.record_dir,
            config_path=args.config,
            credential_file=args.credential_file,
            hermes_root=args.hermes_root,
        )
    except (OSError, TypeError, ValueError, usage_probe.ProbeError) as error:
        parser.error(str(error))
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
