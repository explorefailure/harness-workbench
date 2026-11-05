"""Account-free native permission/counter observations, not model task evidence."""
import hashlib,http.server,json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
import harness_workbench
from harness_workbench._adapter_controls import validate

class Fixture:
 def __init__(self,hermes=False):self.hermes=hermes
 def __enter__(self):
  self.calls=[];self.workspace=Path('.');owner=self
  class Handler(http.server.BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_GET(self):
    raw=json.dumps({'object':'list','data':[{'id':'fixture-control','object':'model','owned_by':'local'}]}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
   def do_POST(self):
    self.connection.settimeout(5);n=int(self.headers.get('Content-Length','0'))
    if not 0<n<=2097152:self.send_error(413);return
    body=json.loads(self.rfile.read(n))
    if not body.get('stream'):
     raw=json.dumps({'id':'local-probe','object':'chat.completion','model':body.get('model','fixture-control'),'choices':[{'index':0,'message':{'role':'assistant','content':'Local fixture.'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
    i=sum(m.get('role')=='tool' for m in body.get('messages',[]));owner.calls.append(body)
    if i>5:self.send_error(429);return
    tools=[('read',{'filePath':str(owner.workspace/'target.txt')}),('edit',{'filePath':str(owner.workspace/'target.txt'),'oldString':'alpha','newString':'beta'})]
    if owner.hermes:tools=[('terminal',{'command':'pwd','timeout':5}),('read_file',{'path':'target.txt'}),('patch',{'path':'target.txt','old_string':'alpha','new_string':'beta'})]
    if i<len(tools):
     tool,args=tools[i];delta={'role':'assistant','tool_calls':[{'index':0,'id':'controlled-'+str(i),'type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]}
    else:delta={'role':'assistant','content':'Done.'}
    def chunk(d,finish):return 'data: '+json.dumps({'id':'local','object':'chat.completion.chunk','created':1,'model':body['model'],'choices':[{'index':0,'delta':d,'finish_reason':finish}]})+'\n\n'
    raw=(chunk(delta,None)+chunk({},'tool_calls' if i<len(tools) else 'stop')+'data: [DONE]\n\n').encode();self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.endpoint=f'http://127.0.0.1:{self.server.server_port}/v1';return self
 def __exit__(self,*a):self.server.shutdown();self.server.server_close();self.thread.join(5)

def exercise(out,variant):
 out.mkdir();env=dict(os.environ,PYTHONPATH=str(Path(harness_workbench.__file__).parent.parent));commands=[]
 def run(name,args):
  p=subprocess.run([sys.executable,*map(str,args)],env=env,capture_output=True,text=True,timeout=100);(out/(name+'.stdout')).write_text(p.stdout);(out/(name+'.stderr')).write_text(p.stderr);commands.append({'argv':[sys.executable,*map(str,args)],'exit':p.returncode});(out/'commands.json').write_text(json.dumps(commands,indent=2));assert p.returncode==0,p.stdout+p.stderr;return p
 owned=out/'owned';run('init',['-m','harness_workbench._opencode_cli','init',owned]);(owned/'fixture').mkdir();(owned/'fixture/target.txt').write_text('alpha\n');(owned/'control-task.md').write_text('Read target.txt, edit alpha to beta, then stop. Do not use bash.\n')
 with Fixture() as fixture:
  c=json.loads((owned/'adapter_config.json').read_text());c['fixture']='fixture';c['task']='control-task.md';c['inputs']=[n for n in c['inputs'] if not n.startswith('coding_fixture/') and n!='task.md']+['fixture/target.txt','control-task.md','control_runtime.py'];c['provider']={'mode':'local','base_url':fixture.endpoint,'model':'fixture-control'};c['checks']=[{'id':'target','argv':[sys.executable,'-B','-c',"from pathlib import Path;assert Path('target.txt').read_text()=='beta\\n'"],'timeout':5}]
  c['controls']={'schema':'hwb-native-controls/v1','harness':'opencode','values':{'edit_permission':{'permission':'deny' if variant=='enabled' else 'allow','expect':{'path':'target.txt','effect':'changed' if variant in ('baseline','counter-inverted') else 'unchanged'}},'tool_counter':{'tool':'read','enabled':variant!='counter-inverted','minimum':1}}}
  (owned/'adapter_config.json').write_text(json.dumps(c));spec=owned/'experiment.json';run('generate',['-m','harness_workbench._adapter_bridge','spec','opencode',owned/'adapter_config.json','--launcher',os.environ['HWB_OPENCODE_BIN'],'--timeout',('0.05' if variant=='timeout' else '20'),'--output',spec])
  # Native paths are relative to the managed cwd.
  fixture.workspace=Path('.')
  run('run',['-m','harness_workbench','--root',out/'runs','run',spec]);record=next((out/'runs').iterdir());run('verify',['-m','harness_workbench','--root',out/'runs','verify',record.name]);bundle=record/'steps/opencode-custom-task/attempts/0/adapter-evidence';result=json.loads((bundle/'bridge-result.json').read_text());(out/'requests.json').write_text(json.dumps(fixture.calls,indent=2));return result,bundle

class SchemaTests(unittest.TestCase):
 def test_permission_oracle_rejects_no_attempt_wrong_path_and_check_edits(self):
  from harness_workbench._opencode_controls import measure,render
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);request={'schema':'hwb-native-controls/v1','harness':'opencode','values':{'edit_permission':{'permission':'deny','expect':{'path':'target.txt','effect':'unchanged'}}}}
   runtime={'permission':{'edit':'allow'},'plugin':[]};e=render(request,runtime,root);(root/'runtime.json').write_text(json.dumps(runtime,indent=2)+'\n');entry={'path':'target.txt','sha256':'old'}
   record={'summary':{'tools':[{'call_id':'call','tool':'edit','status':'error','input':{'filePath':'target.txt'},'error':"Model tried to call unavailable tool 'invalid'"}]},'workspace':{'before':[entry],'after':[entry],'after_checks':[entry]}}
   self.assertTrue(measure(json.loads(json.dumps(e)),record,root)['effect']['passed'])
   for mutation in ('no-attempt','wrong-path','allowed-error','check-edits'):
    broken=json.loads(json.dumps(record))
    if mutation=='no-attempt':broken['summary']['tools']=[]
    if mutation=='wrong-path':broken['summary']['tools'][0]['input']['filePath']='other.txt'
    if mutation=='allowed-error':broken['summary']['tools'][0]['error']='ordinary edit failed'
    if mutation=='check-edits':broken['workspace']['after_checks'][0]['sha256']='changed'
    self.assertFalse(measure(json.loads(json.dumps(e)),broken,root)['effect']['passed'],mutation)

 def test_owned_control_helpers_must_be_declared_before_runtime(self):
  env=dict(os.environ,PYTHONPATH=str(Path(harness_workbench.__file__).parent.parent))
  with tempfile.TemporaryDirectory() as tmp:
   for harness in ('opencode','hermes'):
    root=Path(tmp)/harness
    p=subprocess.run([sys.executable,'-m','harness_workbench._'+harness+'_cli','init',str(root)],env=env,capture_output=True,text=True);self.assertEqual(0,p.returncode,p.stderr)
    path=root/'adapter_config.json';c=json.loads(path.read_text());c['provider']={'mode':'local','base_url':'http://127.0.0.1:1/v1','model':'fixture-control'}
    values={'tool_counter':{'tool':'read','enabled':True,'minimum':1}} if harness=='opencode' else {'tool_selection':{'toolsets':['file'],'expect_terminal':True}}
    c['controls']={'schema':'hwb-native-controls/v1','harness':harness,'values':values};path.write_text(json.dumps(c))
    for missing_file in (False,True):
     if missing_file:
      c['inputs'].append('control_runtime.py');path.write_text(json.dumps(c));(root/'control_runtime.py').unlink()
     p=subprocess.run([sys.executable,str(root/'adapter.py'),str(path),'--check-only'],env=env,capture_output=True,text=True)
     self.assertEqual(2,p.returncode,p.stdout+p.stderr);self.assertIn('control_runtime.py',p.stdout+p.stderr);self.assertNotIn('Traceback',p.stdout+p.stderr)

 def test_invalid_values(self):
  for values in ({'edit_permission':{'permission':'ask','expect':{'path':'target.txt','effect':'unchanged'}}},{'tool_counter':{'tool':'read','enabled':True,'minimum':True}},{'edit_permission':{'permission':'deny','expect':{'path':'../outside','effect':'unchanged'}}}):
   with self.assertRaises(ValueError):validate({'schema':'hwb-native-controls/v1','harness':'opencode','values':values},'opencode')

@unittest.skipUnless(os.environ.get('HWB_OPENCODE_CONTROL_TEST')=='1','requires pinned OpenCode and owned loopback fixture')
class NativeTests(unittest.TestCase):
 def test_baseline_denial_and_inversions(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(os.environ.get('HWB_OPENCODE_CONTROL_EVIDENCE',tmp));root.mkdir(exist_ok=True)
   for variant in ('baseline','enabled','policy-inverted','counter-inverted','timeout'):
    with self.subTest(variant=variant):
     result,bundle=exercise(root/variant,variant)
     if variant=='timeout':
      self.assertFalse(result['capture_valid'],result);self.assertFalse(result['experiment']['passed'],result);self.assertEqual(result['original_inputs_before'],result['original_inputs_after']);continue
     self.assertTrue(result['capture_valid'],result);self.assertEqual(variant!='enabled',result['task_success'],result);self.assertEqual(variant in ('baseline','enabled'),result['experiment']['passed'],result);self.assertEqual(result['original_inputs_before'],result['original_inputs_after']);self.assertEqual('alpha\n' if variant=='enabled' else 'beta\n',(bundle/'workspace/target.txt').read_text())
