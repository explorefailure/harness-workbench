"""Loopback OpenAI-compatible fixture provider; no inference or cloud service."""
from __future__ import annotations

import http.server
import json
from pathlib import Path
import shlex
import sys
import threading

REPAIRED = '''import re


def slugify(text):
    """Return a URL-safe identifier for a short ASCII label."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
'''


class ScriptedProvider:
    """Bound requests and retain the provider-visible conversation verbatim."""

    def __init__(self, fixture: Path):
        self.requests: list[dict] = []
        self.errors: list[str] = []
        self.probes: list[dict] = []
        self.total_bytes = 0
        self.lock = threading.Lock()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.connection.settimeout(5)
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if self.path not in ('/v1/chat/completions', '/api/show') or not 0 < size <= 1_048_576:
                        raise ValueError(f'unexpected path or request size: {self.path} ({size} bytes)')
                    raw = self.rfile.read(size)
                    with owner.lock:
                        owner.total_bytes += len(raw)
                        if len(owner.requests) + len(owner.probes) >= 32 or owner.total_bytes > 4_194_304:
                            raise ValueError('provider request bound exceeded')
                        body = json.loads(raw)
                        if self.path == '/api/show':
                            if body != {'name': 'repair'}:
                                raise ValueError('unexpected Ollama capability probe body')
                            owner.probes.append({'path': self.path, 'body': body, 'response_status': 404})
                        else:
                            owner.requests.append(body)
                    if self.path == '/api/show':
                        # Hermes probes local endpoints for Ollama metadata. This
                        # fixture is Chat Completions, not an Ollama server. A
                        # recorded 404 is the honest capability-negotiation result.
                        data = json.dumps({'error': 'not an Ollama server'}).encode()
                        self.send_response(404)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    delta, reason = owner.respond(body, fixture)
                    base = {'id': 'chatcmpl-hwb', 'object': 'chat.completion.chunk',
                            'created': 0, 'model': 'repair'}
                    if body.get('stream'):
                        data = ''.join('data: ' + json.dumps(dict(base, choices=[{
                            'index': 0, 'delta': d, 'finish_reason': r}])) + '\n\n'
                            for d, r in [(delta, None), ({}, reason)]) + 'data: [DONE]\n\n'
                        kind = 'text/event-stream'
                    else:
                        message = dict(delta)
                        for tool in message.get('tool_calls', []):
                            tool.pop('index', None)
                        data = json.dumps({'id': 'chatcmpl-hwb', 'object': 'chat.completion',
                            'created': 0, 'model': 'repair', 'choices': [{
                            'index': 0, 'message': message, 'finish_reason': reason}]})
                        kind = 'application/json'
                    self.send_response(200)
                    self.send_header('Content-Type', kind)
                    self.send_header('Content-Length', str(len(data.encode())))
                    self.end_headers()
                    self.wfile.write(data.encode())
                except (ValueError, KeyError, TypeError, OSError) as error:
                    with owner.lock:
                        owner.errors.append(str(error))
                    self.send_error(400, 'scripted provider rejected request')

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @staticmethod
    def respond(body: dict, fixture: Path) -> tuple[dict, str]:
        messages = body['messages']
        # Auxiliary title requests carry no tool definitions.
        if not body.get('tools'):
            return {'role': 'assistant', 'content': 'Workbench repair fixture'}, 'stop'
        results = [m for m in messages if m.get('role') == 'tool']
        stage = len(results)
        if [r.get('tool_call_id') for r in results] != [f'hwb-repair-{n}' for n in range(stage)]:
            raise ValueError('unexpected tool-result sequence')
        test = shlex.quote(sys.executable) + ' -B -m unittest -v'
        plan = [
            ('read_file', {'path': str(fixture / 'slugger.py')}),
            ('terminal', {'command': test, 'workdir': str(fixture), 'timeout': 15}),
            ('patch', {'path': str(fixture / 'slugger.py'),
                      'old_string': (fixture.parent / 'original.py').read_text(), 'new_string': REPAIRED}),
            ('terminal', {'command': test, 'workdir': str(fixture), 'timeout': 15}),
        ]
        if stage == len(plan):
            return {'role': 'assistant', 'content': 'Repair complete; inspect the independent outcome check.'}, 'stop'
        if stage > len(plan):
            raise ValueError('unexpected extra tool result')
        name, arguments = plan[stage]
        available = {t.get('function', {}).get('name') for t in body['tools']}
        if name not in available:
            raise ValueError('required native tool unavailable: ' + name)
        return {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'hwb-repair-{stage}',
            'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}, 'tool_calls'

    def __enter__(self):
        self.thread.start()
        return self

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.server.server_port}/v1'

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
