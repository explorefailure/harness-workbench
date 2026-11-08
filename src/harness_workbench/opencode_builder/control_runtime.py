"""Narrow owned OpenCode local permission/counter profiles; trusted execution."""
from pathlib import Path
import hashlib
import json
from urllib.parse import urlsplit
from harness_workbench._adapter_controls import relative_file, evidence


def validate(values):
    policy=values.get('edit_permission')
    if 'edit_permission' in values:
        if not isinstance(policy,dict) or set(policy)!={'permission','expect'} or policy['permission'] not in ('allow','deny'):
            raise ValueError('edit_permission requires permission allow/deny and expect')
        ex=policy['expect']
        if not isinstance(ex,dict) or set(ex)!={'path','effect'} or ex['effect'] not in ('changed','unchanged'):
            raise ValueError('edit_permission expect requires path and changed/unchanged effect')
        relative_file(ex['path'])
    counter=values.get('tool_counter')
    if 'tool_counter' in values:
        if (not isinstance(counter,dict) or set(counter)!={'tool','enabled','minimum'} or counter['tool'] not in ('read','edit','bash')
                or type(counter['enabled']) is not bool or type(counter['minimum']) is not int or not 1<=counter['minimum']<=100):
            raise ValueError('tool_counter requires read/edit/bash tool, boolean enabled, minimum 1..100')


def validate_config(config, root):
    p=config['provider'];u=urlsplit(p.get('base_url',''))
    if p.get('mode')!='local' or p.get('api_key_env') or u.scheme!='http' or u.hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('OpenCode controls require an unauthenticated HTTP loopback local provider')
    policy=config['controls']['values'].get('edit_permission')
    if policy:
        name=policy['expect']['path'];target=root/config['fixture']/name
        if not target.is_file() or target.is_symlink() or not target.resolve().is_relative_to((root/config['fixture']).resolve()):
            raise ValueError('control target must be an existing retained fixture file')


def render(request, runtime, root):
    result=evidence(request);values=request['values']
    if 'edit_permission' in values:runtime['permission']['edit']=values['edit_permission']['permission']
    counter=values.get('tool_counter')
    if counter:
        trace=root/'control.trace.jsonl'
        # Adapter-owned dependency-free native plugin; user plugins/package profiles stay unsupported.
        code="import { appendFileSync } from 'node:fs';\n"+"const trace="+json.dumps(str(trace))+";\n"+"const emit=x=>appendFileSync(trace,JSON.stringify(x)+'\\n');\n"+"export default async () => { emit({type:'loaded'}); return { 'tool.execute.before': async (input) => { "+("emit({type:'before',tool:input.tool,call_id:input.callID});" if counter['enabled'] else '')+" } }; };\n"
        plugin=root/'control-plugin.mjs';plugin.write_text(code);runtime['plugin']=[plugin.as_uri()]
        result['plugin_sha256']=hashlib.sha256(plugin.read_bytes()).hexdigest()
    result['rendered']={'permission':runtime['permission'].copy(),'plugin':list(runtime['plugin'])}
    result['runtime_sha256']=hashlib.sha256((json.dumps(runtime,indent=2)+'\n').encode()).hexdigest()
    return result


def measure(result, record, root):
    errors=[];observations=[];passed=True;values=result['requested']['values'];tools=record['summary']['tools']
    runtime=root/'runtime.json'
    if hashlib.sha256(runtime.read_bytes()).hexdigest()!=result['runtime_sha256']:errors.append('rendered native configuration changed')
    policy=values.get('edit_permission')
    if policy:
        name=policy['expect']['path'];before={p['path']:p for p in record['workspace']['before']};after={p['path']:p for p in record['workspace']['after']};final={p['path']:p for p in record['workspace'].get('after_checks',record['workspace']['after'])}
        attempts=[t for t in tools if t['tool']=='edit' and t.get('input',{}).get('filePath') in (name,str(root/'fixture'/name))]
        changed=before.get(name)!=after.get(name);stable=after.get(name)==final.get(name)
        denied=bool(attempts) and all(t['status']=='error' and ('unavailable tool' in (t.get('error') or '') or 'permission' in (t.get('error') or '').lower()) for t in attempts)
        allowed=any(t['status']=='completed' and isinstance(t.get('metadata'),dict) and t['metadata'].get('filediff',{}).get('file')==str(root/'fixture'/name) for t in attempts)
        ok=stable and bool(attempts) and ((changed and allowed) if policy['expect']['effect']=='changed' else (not changed and denied))
        observations.append({'control':'edit_permission','attempts':len(attempts),'changed':changed,'checks_preserved_target':stable,'native_denial':denied,'passed':ok});passed=passed and ok
    counter=values.get('tool_counter')
    trace=[]
    if counter:
        try:
            plugin=root/'control-plugin.mjs'
            if hashlib.sha256(plugin.read_bytes()).hexdigest()!=result['plugin_sha256']:raise ValueError('owned plugin changed')
            raw=(root/'control.trace.jsonl').read_bytes()
            if len(raw)>1_048_576:raise ValueError('plugin trace exceeds bound')
            trace=[json.loads(line) for line in raw.splitlines()]
            if not all(isinstance(t,dict) for t in trace):raise ValueError('invalid control trace row')
            if not trace or trace[0]!={'type':'loaded'}:raise ValueError('missing plugin activation')
            native={(t['call_id'],t['tool']) for t in tools};calls=[t for t in trace[1:] if t.get('type')=='before' and t.get('tool')==counter['tool']]
            correlated=[t for t in calls if (t.get('call_id'),t.get('tool')) in native]
            ok=len(correlated)==len(calls) and len(correlated)>=counter['minimum'] and len({t['call_id'] for t in correlated})==len(correlated)
            observations.append({'control':'tool_counter','native_correlated_calls':len(correlated),'passed':ok});passed=passed and ok
        except (OSError,ValueError,TypeError,KeyError) as e:errors.append(str(e));passed=False
    result['activation']={'state':'observed' if tools or trace else 'not_established','observations':trace}
    result['effect']={'state':'measured','passed':passed and not errors,'observations':observations};result['errors']=errors
    return result
