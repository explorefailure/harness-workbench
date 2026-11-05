"""Hermes 0.22.5 stream-json: preserve native IDs/status and require termination."""
import json
from harness_workbench.capture import parse_jsonl


def summarize(raw):
    events, errors = parse_jsonl(raw, objects_only=True)
    initialized = False
    terminal = None
    pending = {}
    seen = set()
    tools = []
    for index, event in enumerate(events):
        try:
            if terminal is not None:
                raise ValueError('event after terminal result')
            kind = event['type']
            if kind == 'system':
                if initialized or index != 0 or event.get('subtype') != 'init':
                    raise ValueError('unexpected system/init event')
                if not isinstance(event.get('model'), str) or not event['model']:
                    raise ValueError('missing model identity')
                initialized = True
                continue
            if not initialized:
                raise ValueError('event before native init')
            if kind == 'tool_use':
                call = event['tool_call_id']
                name = event['name']
                if not isinstance(call, str) or not call or call in seen:
                    raise ValueError('missing or duplicate tool identity')
                if not isinstance(name, str) or not name or not isinstance(event.get('input'), dict):
                    raise ValueError('invalid tool name/input')
                seen.add(call)
                pending[call] = event
            elif kind == 'tool_result':
                call = event['tool_call_id']
                start = pending.pop(call, None)
                if start is None or start['name'] != event['name']:
                    raise ValueError('unmatched tool result identity/name')
                if type(event.get('is_error')) is not bool or not isinstance(event.get('output'), str):
                    raise ValueError('invalid native result status/output')
                payload = None
                try:
                    payload = json.loads(event['output'])
                except ValueError:
                    pass  # Hermes caps native CLI result strings at 5000 chars.
                exit_code = payload.get('exit_code') if isinstance(payload, dict) else None
                if exit_code is not None and type(exit_code) is not int:
                    raise ValueError('invalid terminal exit observation')
                tools.append({'call_id': call, 'tool': event['name'], 'input': start['input'],
                    'is_error': event['is_error'], 'output': event['output'], 'exit_code': exit_code,
                    'output_json_decoded': isinstance(payload, dict)})
            elif kind == 'text':
                if not isinstance(event.get('text'), str):
                    raise ValueError('invalid native text delta')
            elif kind == 'result':
                if pending:
                    raise ValueError('terminal result with incomplete tool pairs')
                if type(event.get('exit_code')) is not int or event['exit_code'] != 0 or event.get('error'):
                    raise ValueError('native terminal result reports failure')
                if not isinstance(event.get('session_id'), str) or not event['session_id']:
                    raise ValueError('missing terminal session identity')
                terminal = event
            else:
                raise ValueError('unsupported native event: ' + str(kind))
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f'event {index}: {error}')
    if not initialized or terminal is None or pending:
        errors.append('missing complete init/tool/terminal stream')
    return {'valid': not errors, 'errors': errors, 'event_count': len(events),
            'tools': tools, 'terminal': terminal,
            'native_output_limit_chars': 5000}
