#!/usr/bin/env python3
"""Loopback OpenAI/Ollama compatibility shim for post-tool continuations."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import sys
import threading
import time
from typing import Callable
import urllib.error
import urllib.request


def _bounded_probe(argv: list[str]) -> tuple[int, str] | None:
    """Run the probe through the Workbench capture primitive, never directly."""
    from harness_workbench import capture

    try:
        result = capture.run_bounded(
            argv, cwd=Path.cwd(), env=dict(os.environ), timeout=15,
            stdout_limit=4096, stderr_limit=4096, forward_signals=False,
        )
    except (OSError, capture.CaptureError):
        return None
    if result.termination_reason is not None:
        return None
    return result.returncode, result.stdout.decode("utf-8", "replace")


class ThermalGate:
    """Hold each model request until the serving GPU has cooled.

    `probe` is an argv whose stdout starts with the temperature in whole degrees
    Celsius (for example `ssh gpu-host nvidia-smi --query-gpu=temperature.gpu
    --format=csv,noheader,nounits`). A request proceeds once a reading is at or
    below `cool_at`. An unreadable temperature counts as hot: an unknown reading
    never lets a model call start. One lock serializes waiting requests.
    """

    def __init__(
        self,
        probe: list[str],
        cool_at: int,
        poll_seconds: float = 5.0,
        *,
        run: Callable[[list[str]], tuple[int, str] | None] = _bounded_probe,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
    ) -> None:
        if not probe:
            raise ValueError("thermal probe argv must not be empty")
        self.probe = list(probe)
        self.cool_at = int(cool_at)
        self.poll_seconds = float(poll_seconds)
        self._run, self._sleep, self._clock = run, sleep, clock
        self._log = log or (lambda line: print(line, file=sys.stderr, flush=True))
        self._lock = threading.Lock()

    def reading(self) -> int | None:
        result = self._run(self.probe)
        if result is None:
            return None
        returncode, stdout = result
        match = re.match(r"\s*(-?\d+)", stdout or "")
        if returncode != 0 or match is None:
            return None
        return int(match.group(1))

    def wait(self) -> dict[str, object]:
        with self._lock:
            started = self._clock()
            readings: list[int | None] = []
            while True:
                celsius = self.reading()
                readings.append(celsius)
                if celsius is not None and celsius <= self.cool_at:
                    waited = round(self._clock() - started, 1)
                    if len(readings) > 1:
                        self._log(
                            f"thermal gate: released at {celsius}C after {waited}s"
                        )
                    return {"waited_seconds": waited, "readings": readings}
                self._log(
                    f"thermal gate: holding request, reading {celsius!r}, "
                    f"need <= {self.cool_at}C"
                )
                self._sleep(self.poll_seconds)


def bridge_tool_continuation(body: bytes) -> bytes:
    """Give Ollama the user turn it requires without losing the original task."""
    payload = json.loads(body)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return body
    has_tool_call = any(
        isinstance(message, dict)
        and message.get("role") == "assistant"
        and message.get("tool_calls")
        for message in messages
    )
    if messages[-1].get("role") != "tool" or not has_tool_call:
        return body
    original_task = next(
        (
            message.get("content", "")
            for message in messages
            if isinstance(message, dict) and message.get("role") == "user"
        ),
        "",
    )
    progress = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant" and message.get("tool_calls"):
            progress.append({"assistant_tool_calls": message["tool_calls"]})
        elif message.get("role") == "tool":
            progress.append({
                "tool_call_id": message.get("tool_call_id"),
                "tool_result": message.get("content"),
            })
    progress_text = json.dumps(progress[-12:], ensure_ascii=True)[:12000]
    messages.append({
        "role": "user",
        "content": (
            f"Original task:\n{original_task}\n\n"
            f"Tool progress so far:\n{progress_text}\n\n"
            "Continuation instruction: this is the same task, not a new session. "
            "The tool progress above is authoritative. Do not inspect unrelated "
            "repository state or repeat completed steps. The word `done` in the "
            "original task is a completion condition, not permission to stop now. "
            "Reply done only when the progress proves every requirement is complete. "
            "If a failing test was expected before an edit, continue to that edit."
        ),
    })
    return json.dumps(payload, separators=(",", ":")).encode()


def handler(
    upstream: str, gate: ThermalGate | None = None, *, bridge: bool = True
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            self._forward(None)

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            stream_response = False
            if self.path == "/v1/chat/completions":
                if bridge:
                    body = bridge_tool_continuation(body)
                stream_response = json.loads(body).get("stream") is True
                if gate is not None:
                    gate.wait()
            self._forward(body, stream_response=stream_response)

        def _forward(
            self, body: bytes | None, *, stream_response: bool = False
        ) -> None:
            headers = {
                key: value for key, value in self.headers.items()
                if key.lower() not in {"host", "content-length", "connection"}
            }
            request = urllib.request.Request(
                upstream.rstrip("/") + self.path,
                data=body,
                headers=headers,
                method=self.command,
            )
            try:
                response = urllib.request.urlopen(request, timeout=1200)
            except urllib.error.HTTPError as error:
                response = error
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in {"content-length", "connection", "transfer-encoding"}:
                    self.send_header(key, value)
            if not stream_response:
                raw = response.read()
                self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                if stream_response:
                    while line := response.readline():
                        self.wfile.write(line)
                        self.wfile.flush()
                else:
                    self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                # A bounded canary intentionally closes after its first event.
                return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=21435)
    parser.add_argument("--upstream", default="http://127.0.0.1:21434")
    parser.add_argument(
        "--thermal-probe",
        help="command printing the serving GPU temperature in C; enables the gate",
    )
    parser.add_argument("--cool-at", type=int, default=78)
    parser.add_argument(
        "--no-tool-bridge", action="store_true",
        help="forward chat bodies unchanged (runtimes that accept post-tool turns)",
    )
    parser.add_argument("--thermal-poll", type=float, default=5.0)
    args = parser.parse_args()
    gate = (
        ThermalGate(shlex.split(args.thermal_probe), args.cool_at, args.thermal_poll)
        if args.thermal_probe else None
    )
    ThreadingHTTPServer(
        ("127.0.0.1", args.listen_port),
        handler(args.upstream, gate, bridge=not args.no_tool_bridge),
    ).serve_forever()


if __name__ == "__main__":
    main()
