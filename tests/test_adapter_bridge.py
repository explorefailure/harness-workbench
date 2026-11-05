"""Bridge behavior and failure boundaries; fake subject is NOT a native test."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import harness_workbench

TEST_ENV = {**os.environ, "PYTHONPATH": str(Path(harness_workbench.__file__).parent.parent)}

from harness_workbench._adapter_bridge import generate, regular, retain, local_request
from harness_workbench._adapter_controls import validate, capabilities
from harness_workbench import spec, features, runner

FAKE = '''import json,sys,tempfile,os
from pathlib import Path
p=Path(sys.argv[1]);c=json.loads(p.read_text());r=Path(tempfile.mkdtemp(dir=sys.argv[sys.argv.index('--workspace-parent')+1]));f=r/'fixture';f.mkdir()
(f/'answer.txt').write_text('changed')
if c.get('fixture_link'):(f/'escape').symlink_to(p)
if c.get('mutate'):(p.parent/'task.md').write_text('mutation')
from harness_workbench.capture import manifest
print(json.dumps({'verdict':{'passed':True},'workspace':{'retained_root':str(r),'after':manifest(f)},'task_checks':{'passed':c.get('check_pass',True)},'env_leak':'BRIDGE_TEST_SECRET' in os.environ,'staged_fixture_exists':(p.parent/c['fixture']).is_dir()}))
raise SystemExit(0 if c.get('check_pass',True) else 1)
'''


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'fixture').mkdir()
        (self.root/'fixture'/'answer.txt').write_text('original')
        (self.root/'adapter.py').write_text(FAKE)
        (self.root/'task.md').write_text('custom task')
        (self.root/'pin.json').write_text('{}')
        self.config = {'schema':'opencode-hwb-config/v0.1', 'fixture':'fixture',
            'pin':'pin.json', 'task':'task.md', 'provider':{'mode':'scripted'},
            'checks':[{'id':'fake-explicit-check','argv':[sys.executable,'-c','pass'],'timeout':1}],
            'inputs':['custom.json','adapter.py','task.md','pin.json','fixture/answer.txt']}

    def generate(self, **changes):
        self.config.update(changes)
        path = self.root/'custom.json'; path.write_text(json.dumps(self.config))
        output = self.root/'experiment.json'
        generate(path,'opencode',output,sys.executable,2)
        return output

    def run_spec(self, path):
        # Use CLI so source and installed command behavior is measured alike.
        proc = subprocess.run([sys.executable,'-m','harness_workbench','--root',str(self.root/'runs'),'run',str(path)],capture_output=True,text=True,env=TEST_ENV)
        self.assertEqual(0,proc.returncode,proc.stderr)
        run = next((self.root/'runs').iterdir())
        bundle = run/'steps'/'opencode-custom-task'/'attempts'/'0'/'adapter-evidence'
        return run,bundle,json.loads((bundle/'bridge-result.json').read_text())

    def test_custom_attempt_is_self_contained_and_tampering_is_detected(self):
        path=self.generate()
        run,bundle,result=self.run_spec(path)
        self.assertTrue(result['capture_valid'],result)
        self.assertTrue(result['task_success'])
        self.assertEqual('changed',(bundle/'workspace'/'answer.txt').read_text())
        self.assertEqual('original',(self.root/'fixture'/'answer.txt').read_text())
        self.assertEqual('clean',runner.verify(str(run))['state'])
        verify=subprocess.run([sys.executable,'-m','harness_workbench','--root',str(run.parent),'verify',run.name],capture_output=True,text=True,env=TEST_ENV)
        self.assertEqual(0,verify.returncode,verify.stdout+verify.stderr)
        (bundle/'workspace'/'answer.txt').write_text('tampered')
        self.assertEqual('drifted',runner.verify(str(run))['state'])
        verify=subprocess.run([sys.executable,'-m','harness_workbench','--root',str(run.parent),'verify',run.name],capture_output=True,text=True,env=TEST_ENV)
        self.assertNotEqual(0,verify.returncode)

    def test_check_failure_is_visible_despite_completed_recorder(self):
        run,bundle,result=self.run_spec(self.generate(check_pass=False))
        self.assertTrue(result['capture_valid'],result)
        self.assertFalse(result['task_success'])
        self.assertEqual('completed',json.loads((run/'record.json').read_text())['status'])
        attempt=json.loads((run/'attempts.jsonl').read_text())
        self.assertEqual(1,attempt['exit'])

    def test_failed_input_mutation_is_retained(self):
        run,bundle,result=self.run_spec(self.generate(mutate=True))
        self.assertFalse(result['capture_valid'])
        self.assertIn('staged source inputs changed',result['errors'])
        self.assertEqual('mutation',(bundle/'inputs'/'task.md').read_text())
        self.assertEqual('custom task',(self.root/'task.md').read_text())

    def test_unsafe_attachment_fails_and_never_follows_link(self):
        run,bundle,result=self.run_spec(self.generate(fixture_link=True))
        self.assertFalse(result['capture_valid'])
        self.assertFalse((bundle/'workspace'/'escape').exists())
        self.assertTrue(any('symbolic link' in e for e in result['errors']))

    def test_source_drift_is_rejected_before_execution(self):
        path=self.generate();(self.root/'task.md').write_text('drift')
        run,bundle,result=self.run_spec(path)
        self.assertFalse(result['capture_valid'])
        self.assertTrue(any('input drift' in e for e in result['errors']))
        self.assertFalse((bundle/'inner-result.json').exists())

    def test_secrets_not_passed_to_subject(self):
        path=self.generate()
        previous=os.environ.get('BRIDGE_TEST_SECRET');os.environ['BRIDGE_TEST_SECRET']='synthetic-private-value'
        TEST_ENV['BRIDGE_TEST_SECRET']='synthetic-private-value'
        try:run,bundle,result=self.run_spec(path)
        finally:
            TEST_ENV.pop('BRIDGE_TEST_SECRET',None)
            if previous is None:os.environ.pop('BRIDGE_TEST_SECRET')
            else:os.environ['BRIDGE_TEST_SECRET']=previous
        self.assertFalse(json.loads((bundle/'inner-result.json').read_text())['env_leak'])
        self.assertNotIn(b'synthetic-private-value',b''.join(p.read_bytes() for p in run.rglob('*') if p.is_file()))

    def test_retry_and_sample_have_fresh_state(self):
        path=self.generate(check_pass=False)
        data=json.loads(path.read_text());data['features'] += [{'name':'retry','config':{'max':2}}, {'name':'sample','config':{'n':2}}]
        path.write_text(json.dumps(data))
        run,bundle,result=self.run_spec(path)
        attempts=[json.loads(s) for s in (run/'attempts.jsonl').read_text().splitlines()]
        self.assertEqual(4,len(attempts))
        for n in range(4):
            b=run/'steps'/'opencode-custom-task'/'attempts'/str(n)/'adapter-evidence'
            self.assertEqual('original',(b/'inputs'/'fixture'/'answer.txt').read_text())
            self.assertEqual('changed',(b/'workspace'/'answer.txt').read_text())

    def test_configuration_and_path_rejections(self):
        for path in ('../task.md','/etc/passwd','fixture/../task.md','./task.md','fixture\\x'):
            with self.subTest(path=path),self.assertRaises(ValueError):regular(self.root,path)
        (self.root/'linked').symlink_to(self.root/'fixture',target_is_directory=True)
        with self.assertRaises(ValueError):regular(self.root,'linked/answer.txt')
        for provider in ({'mode':'native'},{'mode':'local','base_url':'https://example.com/v1'},
                         {'mode':'compatible','base_url':'http://127.0.0.1/v1','api_key_env':'MY_KEY'}):
            with self.assertRaises(ValueError):local_request({**self.config,'provider':provider},'opencode')

        config=self.root/'custom.json';config.write_text(json.dumps(self.config))
        for bad in ({}, 0, '', False):
            with self.subTest(features=bad),self.assertRaises(ValueError):
                generate(config,'opencode',self.root/'invalid-features.json',sys.executable,2,feature_refs=bad)
        self.assertFalse((self.root/'invalid-features.worker.py').exists())

    def test_controls_are_fail_closed(self):
        for harness in ('pi','opencode','hermes'):
            self.assertIsNone(validate(None,harness))
            valid={'schema':'hwb-native-controls/v1','harness':harness,'values':{}}
            self.assertEqual(valid,validate(valid,harness))
            self.assertEqual({'tool_call_extension': 'pi-local-tool-call/v1'} if harness == 'pi' else {'edit_permission':'opencode-local-edit/v1','tool_counter':'opencode-owned-counter/v1'} if harness == 'opencode' else {'tool_selection':'hermes-local-toolsets/v1','patch_hook':'hermes-owned-patch-hook/v1'},capabilities(harness)['implemented_values'])
            for bad in ({**valid,'values':{'tools':['read']}},{**valid,'harness':'other'},
                        {**valid,'extra':True},{**valid,'schema':'future'},[],True):
                with self.subTest(harness=harness,bad=bad),self.assertRaises(ValueError):validate(bad,harness)

    def test_missing_context_and_existing_outputs_refused(self):
        path=self.generate()
        with self.assertRaises(ValueError):generate(self.root/'custom.json','opencode',path,sys.executable,2)
        env=dict(os.environ);env.pop('HWB_ATTEMPT_DIR',None)
        proc=subprocess.run([sys.executable,'-m','harness_workbench._adapter_bridge','execute',str(path.with_suffix('.bridge.json'))],env=env,capture_output=True)
        self.assertEqual(2,proc.returncode)

    def test_retention_bounds_and_special_files(self):
        source=self.root/'huge';source.write_text('x')
        with self.assertRaises(ValueError):retain(source,self.root/'copy',{'files':4096,'bytes':0})
        os.mkfifo(self.root/'fifo')
        with self.assertRaises(ValueError):retain(self.root/'fifo',self.root/'copy',{'files':0,'bytes':0})

    def test_empty_fixture_directory_is_staged(self):
        (self.root/'fixture'/'answer.txt').unlink()
        self.config['inputs'].remove('fixture/answer.txt')
        run,bundle,result=self.run_spec(self.generate())
        self.assertTrue(result['capture_valid'],result)
        self.assertTrue(json.loads((bundle/'inner-result.json').read_text())['staged_fixture_exists'])

    def test_context_timeout_keeps_partial_files_and_records_cleanup(self):
        path=self.generate()
        data=json.loads(path.read_text());data['step_timeout_ms']=300
        data['steps'][0]['argv']=[sys.executable,'-c',
            "import os,time;from pathlib import Path;Path(os.environ['HWB_ATTEMPT_DIR'],'partial.txt').write_text('partial');time.sleep(10)"]
        path.write_text(json.dumps(data))
        proc=subprocess.run([sys.executable,'-m','harness_workbench','--root',str(self.root/'runs'),'run',str(path)],env=TEST_ENV,capture_output=True,text=True,timeout=10)
        self.assertEqual(0,proc.returncode,proc.stderr)
        run=next((self.root/'runs').iterdir())
        attempt=json.loads((run/'attempts.jsonl').read_text())
        self.assertTrue(attempt['timed_out'])
        process=attempt['attempt_context']['process']
        self.assertEqual('timeout',process['termination_reason'])
        self.assertTrue(process['descendant_cleanup']['passed'])
        self.assertEqual('partial',(run/'steps/opencode-custom-task/attempts/0/partial.txt').read_text())
        self.assertEqual('clean',runner.verify(str(run))['state'])

    def test_context_version_and_unbounded_execution_refused(self):
        path=self.generate();data=json.loads(path.read_text())
        data['steps'][0]['attempt_context']='future'
        path.write_text(json.dumps(data))
        with self.assertRaises(spec.SpecError):spec.load(str(path))
        data['steps'][0]['attempt_context']='hwb-attempt/v1';del data['step_timeout_ms']
        path.write_text(json.dumps(data))
        with self.assertRaises(spec.SpecError):spec.load(str(path))

    def test_real_adapter_validators_reject_unknown_controls_before_runtime(self):
        package=Path(harness_workbench.__file__).parent
        for harness in ('pi','opencode','hermes'):
            owned=self.root/harness
            shutil.copytree(package/(harness+'_builder'),owned,ignore=shutil.ignore_patterns('__pycache__','runtime'))
            name='coding_adapter_config.json' if harness=='pi' else 'adapter_config.json'
            config=owned/name;data=json.loads(config.read_text())
            data['controls']={'schema':'hwb-native-controls/v1','harness':harness,'values':{'tool_denial':True}}
            config.write_text(json.dumps(data))
            proc=subprocess.run([sys.executable,str(owned/'adapter.py'),str(config),'--check-only'],env=TEST_ENV,capture_output=True,text=True,timeout=10)
            self.assertEqual(2,proc.returncode,proc.stdout+proc.stderr)
            self.assertIn('unsupported native control',proc.stdout+proc.stderr)


if __name__=='__main__':unittest.main()
