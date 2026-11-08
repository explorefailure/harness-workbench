"""Owned Hermes local toolset and patch-hook profile; no arbitrary custom config."""
import hashlib,json,shlex
from pathlib import Path
from urllib.parse import urlsplit
from harness_workbench._adapter_controls import relative_file,evidence


def validate(values):
 if 'tool_selection' in values:
  c=values['tool_selection']
  if not isinstance(c,dict) or set(c)!={'toolsets','expect_terminal'} or not isinstance(c['toolsets'],list) or not c['toolsets'] or len(set(c['toolsets']))!=len(c['toolsets']) or any(t not in ('file','terminal') for t in c['toolsets']) or c['expect_terminal'] is not True:raise ValueError('tool_selection requires unique file/terminal toolsets and expect_terminal=true; native filtered-call absence is not observable')
 if 'patch_hook' in values:
  c=values['patch_hook']
  if not isinstance(c,dict) or set(c)!={'block','expect','timeout'} or type(c['block']) is not bool or type(c['timeout']) is not int or not 1<=c['timeout']<=5:raise ValueError('patch_hook requires block, expect and timeout 1..5 seconds')
  ex=c['expect']
  if not isinstance(ex,dict) or set(ex)!={'path','effect'} or ex['effect'] not in ('changed','unchanged'):raise ValueError('patch_hook expect requires relative path and changed/unchanged effect')
  relative_file(ex['path'])


def validate_config(config,root):
 p=config['provider'];u=urlsplit(p.get('base_url',''))
 if p.get('mode')!='local' or p.get('api_key_env') or u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('Hermes controls require an unauthenticated HTTP loopback local provider')
 hook=config['controls']['values'].get('patch_hook')
 if hook:
  target=root/config['fixture']/hook['expect']['path']
  if not target.is_file() or target.is_symlink() or not target.resolve().is_relative_to((root/config['fixture']).resolve()):raise ValueError('hook target must be an existing fixture file')


def render(request,root,env,argv,python):
 result=evidence(request);values=request['values'];selection=values.get('tool_selection')
 if selection:argv[argv.index('--toolsets')+1]=','.join(selection['toolsets'])
 hook=values.get('patch_hook');bound={}
 if hook:
  # A new explicit owned profile replaces safe-mode, never the user's profile.
  # Project/bundled/plugin discovery is suppressed; only this generated shell hook is accepted.
  argv.remove('--safe-mode');argv.append('--accept-hooks');empty=root/'empty-plugins';empty.mkdir()
  env.update({'HERMES_IGNORE_RULES':'1','HERMES_ENABLE_PROJECT_PLUGINS':'0','HERMES_BUNDLED_PLUGINS':str(empty)})
  script=root/'control-hook.py';trace=root/'control.trace.jsonl'
  code="""import json,sys
from pathlib import Path
p=json.load(sys.stdin)
e=p.get('extra') or {}
row={'type':p.get('hook_event_name'),'tool':p.get('tool_name'),'path':(p.get('tool_input') or {}).get('path'),'cwd':p.get('cwd'),'session_id':p.get('session_id'),'call_id':e.get('tool_call_id'),'status':e.get('status'),'error_type':e.get('error_type'),'block':BLOCK}
Path(TRACE).open('a').write(json.dumps(row)+'\\n')
print(json.dumps({'action':'block' if BLOCK else 'continue','message':'Declared Workbench patch control'}) if p.get('hook_event_name')=='pre_tool_call' else '{}')
""".replace('BLOCK',repr(hook['block'])).replace('TRACE',repr(str(trace)))
  script.write_text(code);entry={'command':shlex.join([str(python),str(script)]),'matcher':'patch','timeout':hook['timeout'],'fail_closed':True}
  configuration={'plugins':{'enabled':[]},'mcp_servers':{},'hooks':{'pre_tool_call':[entry],'post_tool_call':[entry]}}
  cfg=Path(env['HERMES_HOME'])/'config.yaml';cfg.write_text(json.dumps(configuration,indent=2)+'\n');(root/'control-config.json').write_text(json.dumps(configuration,indent=2)+'\n')
  bound={str(script):hashlib.sha256(script.read_bytes()).hexdigest(),str(cfg):hashlib.sha256(cfg.read_bytes()).hexdigest()}
 result['owned_profile_files']=bound;result['rendered']={'toolsets':argv[argv.index('--toolsets')+1],'profile':'owned-patch-hook/v1' if hook else 'legacy-safe-mode','ignore_rules':True};return result


def measure(result,record,root):
 errors=[];observations=[];passed=True;tools=record['summary']['tools'];values=result['requested']['values'];trace=[]
 for name,digest in result['owned_profile_files'].items():
  p=Path(name)
  if not p.is_file() or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:errors.append('owned Hermes control profile changed')
 selection=values.get('tool_selection')
 if selection:
  attempted=[t for t in tools if t['tool']=='terminal'];available=any(not t['is_error'] for t in attempted);unavailable=any(t['is_error'] and any(x in t['output'].lower() for x in ('not available','not found','unknown tool','not enabled','not in')) for t in attempted);ok=bool(attempted) and (available if selection['expect_terminal'] else unavailable);observations.append({'control':'tool_selection','attempts':len(attempted),'available':available,'unavailable':unavailable,'passed':ok});passed=passed and ok
 hook=values.get('patch_hook')
 if hook:
  try:
   raw=(root/'control.trace.jsonl').read_bytes()
   if len(raw)>1048576:raise ValueError('hook trace exceeds bound')
   trace=[json.loads(line) for line in raw.splitlines()]
   if not all(isinstance(t,dict) for t in trace):raise ValueError('invalid control trace row')
   name=hook['expect']['path'];native=[t for t in tools if t['tool']=='patch' and t['input'].get('path') in (name,str(root/'fixture'/name))]
   pre=[t for t in trace if t.get('type')=='pre_tool_call' and t.get('tool')=='patch' and t.get('path') in (name,str(root/'fixture'/name))]
   post=[t for t in trace if t.get('type')=='post_tool_call' and t.get('tool')=='patch' and t.get('path') in (name,str(root/'fixture'/name))]
   ids=[t.get('call_id') for t in pre];posts={t.get('call_id'):t for t in post};native_ids={t['call_id'] for t in native}
   correlated=bool(ids) and all(isinstance(i,str) and i and i in posts for i in ids) and len(set(ids))==len(ids) and len(posts)==len(ids)
   before={p['path']:p for p in record['workspace']['before']};after={p['path']:p for p in record['workspace']['after']};final={p['path']:p for p in record['workspace'].get('after_checks',record['workspace']['after'])}
   changed=before.get(name)!=after.get(name);stable=after.get(name)==final.get(name)
   denied=correlated and all(t.get('block') is True and posts[t['call_id']].get('status')=='blocked' and posts[t['call_id']].get('error_type')=='plugin_block' and posts[t['call_id']].get('session_id')==t.get('session_id') for t in pre)
   allowed=correlated and set(ids)==native_ids and any(not t['is_error'] and json.loads(t['output']).get('resolved_path')==str(root/'fixture'/name) for t in native)
   ok=correlated and stable and ((changed and allowed) if hook['expect']['effect']=='changed' else (not changed and denied))
   observations.append({'control':'patch_hook','attempts':len(pre),'streamed_patch_executions':len(native),'correlated_native_post_hooks':len(post),'changed':changed,'checks_preserved_target':stable,'passed':ok});passed=passed and ok

  except (OSError,ValueError,TypeError,KeyError) as e:errors.append(str(e));passed=False
 result['activation']={'state':'observed' if tools or trace else 'not_established','observations':trace};result['effect']={'state':'measured','passed':passed and not errors,'observations':observations};result['errors']=errors;return result
