#!/usr/bin/env python3
"""Loopback OpenAI/Ollama compatibility shim for post-tool continuations."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import urllib.error
import urllib.request


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


def handler(upstream: str) -> type[BaseHTTPRequestHandler]:
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
                body = bridge_tool_continuation(body)
                stream_response = json.loads(body).get("stream") is True
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
    args = parser.parse_args()
    ThreadingHTTPServer(
        ("127.0.0.1", args.listen_port), handler(args.upstream)
    ).serve_forever()


if __name__ == "__main__":
    main()
