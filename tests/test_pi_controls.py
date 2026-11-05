"""First native Pi tool-call control. Fixtures are not model-quality evidence."""
import contextlib
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

import harness_workbench
from harness_workbench._adapter_controls import validate

GUARD = "import { decide } from './decision.mjs';\nexport default (event, settings) => event.toolName === 'edit' ? {block:decide(settings), reason:'Declared test control'} : undefined;\n"
HELPER = 'export const decide = settings => settings.block;\n'


class EditFixture:
    def __enter__(self):
        self.calls=[];owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.connection.settimeout(5)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=2_097_152:self.send_error(413);return
                body=json.loads(self.rfile.read(length));owner.calls.append(body)
                if len(owner.calls)>8:self.send_error(429);return
                done=any(m.get('role')=='tool' for m in body.get('messages',[]))
                delta={'role':'assistant','content':'Edit complete.'} if done else {
                    'role':'assistant','tool_calls':[{'index':0,'id':'controlled-edit','type':'function',
                    'function':{'name':'edit','arguments':json.dumps({'path':'target.txt','oldText':'alpha','newText':'beta'})}}]}
                def chunk(d,finish):return 'data: '+json.dumps({'id':'local-control','object':'chat.completion.chunk','created':1,
                    'model':body['model'],'choices':[{'index':0,'delta':d,'finish_reason':finish}]})+'\n\n'
                payload=(chunk(delta,None)+chunk({},'stop' if done else 'tool_calls')+'data: [DONE]\n\n').encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.endpoint=f'http://127.0.0.1:{self.server.server_port}/v1'
        return self
    def __exit__(self,*args):
        self.server.shutdown();self.server.server_close();self.thread.join(5)


def command(python,args,env,output=None,name='command',timeout=160):
    result=subprocess.run([python,*map(str,args)],env=env,capture_output=True,text=True,timeout=timeout)
    if output:
        (output/(name+'.stdout')).write_text(result.stdout)
        (output/(name+'.stderr')).write_text(result.stderr)
        with (output/'commands.jsonl').open('a') as f:f.write(json.dumps({'argv':[python,*map(str,args)],'exit':result.returncode})+'\n')
    return result


def prepare(root, python, env, endpoint, variant, output=None):
    p=command(python,['-m','harness_workbench._pi_cli','init',root],env,output,'init');assert p.returncode==0,p.stderr
    check=json.dumps([python,'-B','-c',"from pathlib import Path; assert Path('target.txt').read_text() == 'beta\\n'"])
    p=command(python,['-m','harness_workbench._pi_cli','configure',root,'--base-url',endpoint,'--model','fixture-control',
        '--context-window','65536','--max-tokens','1024','--check',check],env,output,'configure');assert p.returncode==0,p.stderr
    config_path=root/'model_adapter_config.json';config=json.loads(config_path.read_text())
    config['fixture']='fixture';config['task']='control-task.md'
    (root/'fixture').mkdir();(root/'fixture/target.txt').write_text('alpha\n')
    (root/'control-task.md').write_text('Use the native edit tool exactly once to replace alpha with beta in target.txt. Do not use bash or write. If edit is blocked, stop. Then report the outcome.\n')
    (root/'guard.mjs').write_text(GUARD);(root/'decision.mjs').write_text('export const decide = settings => false;\n' if variant=='inverted' else HELPER)
    if variant=='load-error':(root/'guard.mjs').write_text('not valid JavaScript {{{')
    if variant=='undeclared-import':(root/'guard.mjs').write_text("import './unbound.mjs';\n"+GUARD)
    if variant=='invalid-stream':(root/'guard.mjs').write_text("import { writeSync } from 'node:fs'; export default () => { writeSync(1, 'corrupt debug line\\n'); return {block:false}; };\n")
    if variant=='sync-hang':(root/'guard.mjs').write_text('export default () => { while (true) {} };\n')
    if variant=='timeout':(root/'guard.mjs').write_text('export default async () => new Promise(() => {});\n')
    if variant=='throw':(root/'guard.mjs').write_text("export default () => { throw new Error('deliberate failure'); };\n")
    if variant=='invalid-result':(root/'guard.mjs').write_text("export default () => ({block:false, provider:'wrong'});\n")
    files={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in ['guard.mjs','decision.mjs']}
    config['controls']={'schema':'hwb-native-controls/v1','harness':'pi','values':{'tool_call_extension':{
        'entry':'guard.mjs','files':files,'settings':{'block':variant!='baseline'},
        'expect':{'tool':'edit','path':'target.txt','effect':'changed' if variant=='baseline' else 'unchanged'},'timeout_ms':100}}}
    config['inputs']=[n for n in config['inputs'] if not n.startswith('coding_fixture/') and n!='coding_task.md']
    config['inputs']+=['control-task.md','fixture/target.txt','guard.mjs','decision.mjs','control_runtime.py','tool_call_control.mjs']
    config_path.write_text(json.dumps(config,indent=2)+'\n')
    return config_path


def exercise(output, python, launcher, env, variant, endpoint=None, model=None, native_timeout=20):
    output.mkdir(parents=True,exist_ok=False)
    with EditFixture() if endpoint is None else contextlib.nullcontext(None) as fixture:
        config_path=prepare(output/'owned',python,env,fixture.endpoint if fixture else endpoint,variant,output)
        if model:
            config=json.loads(config_path.read_text());config['provider']['model']=model
            config_path.write_text(json.dumps(config,indent=2)+'\n')
        spec=config_path.parent/'experiment.json'
        result=command(python,['-m','harness_workbench._adapter_bridge','spec','pi',config_path,'--launcher',launcher,'--timeout',str(native_timeout),
            '--features','[{"name":"freeze"},{"name":"receipt"}]','--output',spec],env,output,'generate')
        assert result.returncode==0,result.stderr
        result=command(python,['-m','harness_workbench','--root',output/'runs','run',spec],env,output,'run',timeout=native_timeout+150)
        assert result.returncode==0,result.stdout+result.stderr
        run=next((output/'runs').iterdir());bundle=run/'steps/pi-custom-task/attempts/0/adapter-evidence'
        result=command(python,['-m','harness_workbench','--root',output/'runs','show',run.name],env,output,'show');assert result.returncode==0,result.stderr
        result=command(python,['-m','harness_workbench','--root',output/'runs','verify',run.name],env,output,'verify');assert result.returncode==0,result.stdout+result.stderr
        result=json.loads((bundle/'bridge-result.json').read_text())
        inner=json.loads((bundle/'inner-result.json').read_text()) if (bundle/'inner-result.json').is_file() else None
        request={'variant':variant,'scope':'local model' if endpoint else 'scripted protocol fixture; no authentication or model-quality claim',
                 'bridge':result,'requests':fixture.calls if fixture else None,'inner_complete':inner is not None}
        (output/'observation.json').write_text(json.dumps(request,indent=2)+'\n')
        return result,inner,bundle


class ControlSchemaTests(unittest.TestCase):
    def test_hash_paths_settings_and_unknown_fields_fail_closed(self):
        base={'schema':'hwb-native-controls/v1','harness':'pi','values':{'tool_call_extension':{
            'entry':'guard.mjs','files':{'guard.mjs':'0'*64},'settings':{},
            'expect':{'tool':'edit','path':'target.txt','effect':'unchanged'},'timeout_ms':100}}}
        self.assertEqual(base,validate(base,'pi'))
        for changes in ({'entry':'../guard.mjs'},{'files':{'guard.mjs':'not-a-hash'}},{'timeout_ms':True},{'timeout_ms':0},
                        {'settings':[]},{'extra':True},{'expect':{'tool':'bash','path':'target.txt','effect':'unchanged'}}):
            broken=json.loads(json.dumps(base));broken['values']['tool_call_extension'].update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate(broken,'pi')

    def test_effect_oracle_requires_native_decision_path_and_stable_checked_file(self):
        import importlib.util
        path=Path(harness_workbench.__file__).parent/'pi_builder/control_runtime.py'
        spec=importlib.util.spec_from_file_location('test_control_runtime',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        control={'expect':{'tool':'edit','path':'target.txt','effect':'unchanged'}}
        before=[{'path':'target.txt','sha256':'old'}]
        trace={'jsonl':[{'type':'loaded'},{'type':'decision','call_id':'call','tool':'edit','path':'target.txt','block':True}], 'errors':[]}
        summary={'projection':{'tool_executions':[{'tool_call_id':'call','tool_name':'edit','target_path':'target.txt','is_error':True}]}}
        workspace=Path('/tmp/synthetic-control-oracle')
        result=module.measure(control,trace,summary,before,before,before,workspace)
        self.assertTrue(result['effect']['passed'])
        for variant in ('no-attempt','allowed-error','wrong-native-path','checks-change'):
            t=json.loads(json.dumps(trace));s=json.loads(json.dumps(summary));final=before
            if variant=='no-attempt':t['jsonl']=t['jsonl'][:1]
            if variant=='allowed-error':t['jsonl'][1]['block']=False
            if variant=='wrong-native-path':s['projection']['tool_executions'][0]['target_path']='other.txt'
            if variant=='checks-change':final=[{'path':'target.txt','sha256':'changed-by-check'}]
            self.assertFalse(module.measure(control,t,s,before,before,final,workspace)['effect']['passed'],variant)

    def test_conflicting_invocation_and_undeclared_or_changed_files_fail_before_runtime(self):
        python=sys.executable;env=dict(os.environ,PYTHONPATH=str(Path(harness_workbench.__file__).parent.parent))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'owned';config_path=prepare(root,python,env,'http://127.0.0.1:1/v1','enabled')
            original=config_path.read_text()
            for mutation in ('args','missing','hash','auth','link'):
                config=json.loads(original)
                if mutation=='args':config['pi_arguments']+=['--models','unbound.json']
                if mutation=='missing':config['inputs'].remove('decision.mjs')
                if mutation=='hash':config['controls']['values']['tool_call_extension']['files']['guard.mjs']='0'*64
                if mutation=='auth':config['provider']['api_key_env']='DO_NOT_READ'
                if mutation=='link':
                    (root/'guard.mjs').unlink();(root/'guard.mjs').symlink_to(root/'decision.mjs')
                config_path.write_text(json.dumps(config))
                result=command(python,[root/'adapter.py',config_path,'--pi','/definitely/not-a-runtime','--check-only'],env)
                self.assertEqual(2,result.returncode,result.stdout+result.stderr)
                self.assertNotIn('executable not found',result.stdout+result.stderr)
                self.assertNotIn('Traceback',result.stdout+result.stderr)
            config_path.write_text(original)
            (root/'control_runtime.py').unlink()
            result=command(python,[root/'adapter.py',config_path,'--check-only'],env)
            self.assertEqual(2,result.returncode)
            self.assertNotIn('Traceback',result.stdout+result.stderr)


@unittest.skipUnless(os.environ.get('HWB_PI_CONTROL_TEST')=='1','requires pinned Pi/Node and owned loopback fixture')
class NativeControlTests(unittest.TestCase):
    def test_baseline_enabled_inverted_and_failure_cases(self):
        python=sys.executable;env=dict(os.environ)
        # Installed qualification explicitly removes checkout imports.
        env['PYTHONPATH']=str(Path(harness_workbench.__file__).parent.parent)
        parent=os.environ.get('HWB_PI_CONTROL_EVIDENCE')
        with contextlib.ExitStack() as stack:
            if parent:root=Path(parent);root.mkdir(parents=True,exist_ok=False)
            else:root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            for variant in ('baseline','enabled','inverted','load-error','undeclared-import','timeout','throw','invalid-result','sync-hang','invalid-stream'):
                with self.subTest(variant=variant):
                    result,inner,bundle=exercise(root/variant,python,os.environ['HWB_PI_CONTROL_LAUNCHER'],env,variant,native_timeout=1 if variant=='sync-hang' else 20)
                    self.assertIsNotNone(inner,result)
                    self.assertEqual(result['original_inputs_before'],result['original_inputs_after'])
                    value=(bundle/'workspace/target.txt').read_text()
                    if variant in ('baseline','enabled','inverted'):
                        self.assertTrue(result['capture_valid'],result)
                        self.assertEqual(variant!='inverted',result['experiment']['passed'],result)
                        self.assertEqual(variant!='enabled',result['task_success'],result)
                        self.assertEqual('alpha\n' if variant=='enabled' else 'beta\n',value)
                    else:
                        self.assertFalse(result['capture_valid'],result)
                        self.assertEqual('beta\n' if variant=='invalid-stream' else 'alpha\n',value)
                    self.assertTrue((bundle/'native/control.trace.jsonl').exists())
                    self.assertFalse(inner['pi']['post_cleanup_group_alive'])
                    if variant != 'invalid-stream':
                        self.assertEqual({'fixture-control'}, {e['message']['model'] for e in map(json.loads,(inner['pi']['stdout_jsonl'] or '').splitlines()) if e.get('type')=='message_end' and e.get('message',{}).get('role')=='assistant'})
                    else:
                        self.assertTrue(any('normalizer' in e for e in inner['verdict']['errors']))


if __name__=='__main__':unittest.main()
