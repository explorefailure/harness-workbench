"""Validate only OpenCode 1.19.5 CLI JSON events; raw bytes remain primary."""
from harness_workbench.capture import parse_jsonl


def summarize(raw: bytes):
    events, errors = parse_jsonl(raw, objects_only=True)
    session = None
    active = False
    tools = []
    seen = set()
    reasons = []
    step_tools = 0
    for index, event in enumerate(events):
        try:
            if reasons and reasons[-1] == 'stop':
                raise ValueError('event after terminal stop')
            kind = event['type']
            sid = event['sessionID']
            if not isinstance(sid, str) or not sid:
                raise ValueError('missing session identity')
            if session is None:
                session = sid
            if sid != session:
                raise ValueError('mixed session identities')
            if kind == 'error':
                raise ValueError('native session error')
            part = event['part']
            if not isinstance(part, dict) or part.get('sessionID') != sid:
                raise ValueError('part session mismatch')
            if kind == 'step_start':
                if active or part.get('type') != 'step-start':
                    raise ValueError('unbalanced step start')
                active = True
                step_tools = 0
            elif kind == 'step_finish':
                if not active or part.get('type') != 'step-finish':
                    raise ValueError('unbalanced step finish')
                reason = part['reason']
                if reason not in ('stop', 'tool-calls'):
                    raise ValueError('unsupported finish reason')
                if (reason == 'tool-calls') != (step_tools > 0):
                    raise ValueError('finish reason disagrees with observed tools')
                reasons.append(reason)
                active = False
            elif kind == 'tool_use':
                if not active or part.get('type') != 'tool':
                    raise ValueError('tool outside an active step')
                call = part['callID']
                state = part['state']
                if not isinstance(call, str) or not call or call in seen:
                    raise ValueError('missing or duplicate tool-call identity')
                if state['status'] not in ('completed', 'error'):
                    raise ValueError('nonterminal tool state')
                if not isinstance(part['tool'], str) or not isinstance(state['input'], dict):
                    raise ValueError('invalid tool identity/input')
                metadata = state.get('metadata')
                if part['tool'] == 'bash' and state['status'] == 'completed':
                    if not isinstance(metadata, dict) or type(metadata.get('exit')) is not int:
                        raise ValueError('completed bash lacks integer exit observation')
                    if not isinstance(state['input'].get('command'), str):
                        raise ValueError('bash command is not a string')
                seen.add(call)
                step_tools += 1
                tools.append({'call_id': call, 'tool': part['tool'], 'status': state['status'],
                    'input': state['input'], 'metadata': state.get('metadata'),
                    'output': state.get('output'), 'error': state.get('error')})
            elif kind in ('text', 'reasoning'):
                if not active or part.get('type') != kind or not isinstance(part.get('text'), str):
                    raise ValueError('invalid text/reasoning event')
            else:
                raise ValueError('unsupported event type: ' + str(kind))
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f'event {index}: {error}')
    if active or not reasons or reasons[-1] != 'stop':
        errors.append('missing balanced final stop')
    return {'valid': not errors, 'errors': errors, 'session_id': session,
            'event_count': len(events), 'finish_reasons': reasons, 'tools': tools}
