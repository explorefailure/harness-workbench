"""Owned Hermes control profile: native fixture evidence, not model quality."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
import harness_workbench
from harness_workbench._adapter_controls import validate
from test_opencode_controls import Fixture

def exercise(out,variant):
 out.mkdir();env=dict(os.environ,PYTHONPATH=str(Path(harness_workbench.__file__).parent.parent));commands=[]
 def run(name,args):
  p=subprocess.run([sys.executable,*map(str,args)],env=env,capture_output=True,text=True,timeout=150);(out/(name+'.stdout')).write_text(p.stdout);(out/(name+'.stderr')).write_text(p.stderr);commands.append({'argv':[sys.executable,*map(str,args)],'exit':p.returncode});(out/'commands.json').write_text(json.dumps(commands,indent=2));assert p.returncode==0,p.stdout+p.stderr;return p
 owned=out/'owned';run('init',['-m','harness_workbench._hermes_cli','init',owned]);(owned/'fixture').mkdir();(owned/'fixture/target.txt').write_text('alpha\n');(owned/'control-task.md').write_text('Run pwd, read target.txt, patch alpha to beta and stop.\n')
 with Fixture(hermes=True) as fixture:
  c=json.loads((owned/'adapter_config.json').read_text());c['fixture']='fixture';c['task']='control-task.md';c['inputs']=[n for n in c['inputs'] if not n.startswith('coding_fixture/') and n!='task.md']+['fixture/target.txt','control-task.md','control_runtime.py'];c['provider']={'mode':'local','base_url':fixture.endpoint,'model':'fixture-control'};c['checks']=[{'id':'target','argv':[sys.executable,'-B','-c',"from pathlib import Path;assert Path('target.txt').read_text()=='beta\\n'"],'timeout':5}]
  c['controls']={'schema':'hwb-native-controls/v1','harness':'hermes','values':{'tool_selection':{'toolsets':['file'] if variant=='selection-inverted' else ['file','terminal'],'expect_terminal':True},'patch_hook':{'block':variant=='enabled','expect':{'path':'target.txt','effect':'unchanged' if variant in ('enabled','hook-inverted') else 'changed'},'timeout':1}}}
  if variant.startswith('hook-failure-'):
   adapter=owned/'adapter.py';text=adapter.read_text();needle="            record['controls'] = render(config['controls'], root, env, argv, python)"
   mutation={
    'hook-failure-error':"            (root/'control-hook.py').write_text(\"raise RuntimeError('deliberate hook failure')\\n\")",
    'hook-failure-timeout':"            (root/'control-hook.py').write_text('import time; time.sleep(30)\\n')",
    'hook-failure-missing':"            (root/'control-hook.py').unlink()",
    'hook-failure-matcher':"            p=Path(env['HERMES_HOME'])/'config.yaml'; v=json.loads(p.read_text()); v['hooks']['pre_tool_call'][0]['matcher']='absent_tool'; p.write_text(json.dumps(v))"
   }[variant]
   assert text.count(needle)==1;adapter.write_text(text.replace(needle,needle+'\n'+mutation))
  (owned/'adapter_config.json').write_text(json.dumps(c));spec=owned/'experiment.json';run('generate',['-m','harness_workbench._adapter_bridge','spec','hermes',owned/'adapter_config.json','--launcher',os.environ['HWB_HERMES_RUNTIME'],'--uv',os.environ['HWB_HERMES_UV'],'--timeout','45','--output',spec]);run('run',['-m','harness_workbench','--root',out/'runs','run',spec]);record=next((out/'runs').iterdir());run('verify',['-m','harness_workbench','--root',out/'runs','verify',record.name]);bundle=record/'steps/hermes-custom-task/attempts/0/adapter-evidence';result=json.loads((bundle/'bridge-result.json').read_text());(out/'requests.json').write_text(json.dumps(fixture.calls,indent=2));return result,bundle

class SchemaTests(unittest.TestCase):
 def test_owned_hook_oracle_requires_correlated_native_block(self):
  from harness_workbench._hermes_controls import measure
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);entry={'path':'target.txt','sha256':'old'}
   e={'requested':{'values':{'patch_hook':{'block':True,'expect':{'path':'target.txt','effect':'unchanged'},'timeout':1}}},'owned_profile_files':{}}
   pre={'type':'pre_tool_call','tool':'patch','path':'target.txt','call_id':'call','session_id':'session','block':True};post={**pre,'type':'post_tool_call','status':'blocked','error_type':'plugin_block'}
   record={'summary':{'tools':[]},'workspace':{'before':[entry],'after':[entry],'after_checks':[entry]}}
   for mutation in ('valid','no-post','wrong-id','wrong-session','wrong-status','check-edits','invalid-row'):
    rows=[pre,post];rec=json.loads(json.dumps(record))
    if mutation=='no-post':rows=[pre]
    if mutation=='wrong-id':rows=[pre,{**post,'call_id':'other'}]
    if mutation=='wrong-session':rows=[pre,{**post,'session_id':'other'}]
    if mutation=='wrong-status':rows=[pre,{**post,'status':'success'}]
    if mutation=='check-edits':rec['workspace']['after_checks'][0]['sha256']='changed'
    if mutation=='invalid-row':rows=['corrupt']
    (root/'control.trace.jsonl').write_text(''.join(json.dumps(t)+'\n' for t in rows))
    self.assertEqual(mutation=='valid',measure(json.loads(json.dumps(e)),rec,root)['effect']['passed'],mutation)

 def test_invalid_values(self):
  for values in ({'tool_selection':{'toolsets':['file'],'expect_terminal':False}},{'tool_selection':{'toolsets':['file'],'expect_terminal':1}},{'tool_selection':{'toolsets':['all'],'expect_terminal':True}},{'patch_hook':{'block':True,'expect':{'path':'../outside','effect':'unchanged'},'timeout':1}},{'tool_selection':{'toolsets':['file'],'expect_terminal':'true'}}):
   with self.assertRaises(ValueError):validate({'schema':'hwb-native-controls/v1','harness':'hermes','values':values},'hermes')

@unittest.skipUnless(os.environ.get('HWB_HERMES_CONTROL_TEST')=='1','requires pinned frozen Hermes runtime and owned fixture')
class NativeTests(unittest.TestCase):
 def test_baseline_denial_and_inversions(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(os.environ.get('HWB_HERMES_CONTROL_EVIDENCE',tmp));root.mkdir(exist_ok=True)
   for variant in ('baseline','enabled','hook-inverted','selection-inverted','hook-failure-error','hook-failure-timeout','hook-failure-missing','hook-failure-matcher'):
    with self.subTest(variant=variant):
     result,bundle=exercise(root/variant,variant)
     if variant.startswith('hook-failure-'):
      self.assertFalse(result['capture_valid'],result);self.assertFalse(result['experiment']['passed'],result);self.assertEqual(result['original_inputs_before'],result['original_inputs_after']);inner=json.loads((bundle/'inner-result.json').read_text());self.assertTrue(inner['gateway_cleanup']['result']['passed']);continue
     self.assertTrue(result['capture_valid'],result);self.assertEqual(variant!='enabled',result['task_success'],result);self.assertEqual(variant in ('baseline','enabled'),result['experiment']['passed'],result);self.assertEqual(result['original_inputs_before'],result['original_inputs_after']);self.assertEqual('alpha\n' if variant=='enabled' else 'beta\n',(bundle/'workspace/target.txt').read_text());inner=json.loads((bundle/'inner-result.json').read_text());self.assertTrue(inner['gateway_cleanup']['result']['passed'])
     terminals=[t for t in inner['summary']['tools'] if t['tool']=='terminal' and not t['is_error']]
     if terminals:self.assertEqual(str(Path(inner['workspace']['retained_root'])/'fixture'),json.loads(terminals[0]['output'])['output'].strip())
