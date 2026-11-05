"""Model selection through native Pi interfaces, not a custom provider extension."""
import contextlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'src/harness_workbench/pi_builder'
sys.path.insert(0, str(ROOT / 'src'))
spec = importlib.util.spec_from_file_location('pi_provider_config', DATA / 'provider_config.py')
providers = importlib.util.module_from_spec(spec);spec.loader.exec_module(providers)


class ProviderTests(unittest.TestCase):
    def command(self, *args, env=None):
        return subprocess.run([sys.executable, '-m', 'harness_workbench._pi_cli', *map(str, args)],
            env=dict(os.environ, PYTHONPATH=str(ROOT / 'src'), **(env or {})),
            capture_output=True, text=True, timeout=90)

    def test_configure_creates_owned_config_without_changing_control(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'builder';self.assertEqual(0,self.command('init',root).returncode)
            before=(root/'coding_adapter_config.json').read_bytes()
            args=('configure',root,'--base-url','http://127.0.0.1:11434/v1','--model','chosen:27b')
            result=self.command(*args);self.assertEqual(0,result.returncode,result.stderr)
            config=json.loads((root/'model_adapter_config.json').read_text())
            self.assertEqual('chosen:27b',config['provider']['model'])
            self.assertEqual([],config['extensions'])
            self.assertIn('model_adapter_config.json',config['inputs'])
            self.assertIn('provider_config.py',config['inputs'])
            self.assertEqual(before,(root/'coding_adapter_config.json').read_bytes())
            self.assertEqual(2,self.command(*args).returncode)
            self.assertEqual(2,self.command(*args,'--config-name','../escape.json').returncode)

    def test_provider_contract_refuses_embedded_secrets_and_ambiguous_limits(self):
        base={'mode':'compatible','model':'chosen','base_url':'http://localhost/v1'}
        for changes in ({'api_key':'literal'}, {'api_key_env':'literal key'}, {'base_url':'http://u:p@localhost/v1'},
                {'base_url':'http://localhost/v1?token=x'}, {'base_url':False}, {'model':''}, {'model':'two models'},
                {'max_tokens':True}, {'context_window':10}, {'api':'unreviewed'}, {'mode':'unknown'}):
            with self.assertRaises(ValueError,msg=changes):providers.validate(dict(base,**changes))
        with self.assertRaises(ValueError):providers.validate({'mode':'native','name':'anthropic','model':'chosen'})

    def test_missing_credential_fails_without_value_or_account_fallback(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{},clear=True):
            provider={'mode':'compatible','model':'chosen','base_url':'http://localhost/v1','api_key_env':'MY_CHOSEN_KEY'}
            with self.assertRaisesRegex(ValueError,'missing or empty'):providers.prepare(provider,Path(temp))
            self.assertFalse((Path(temp)/'models.json').exists())

    def test_native_and_compatible_profiles_store_references_only(self):
        secret='synthetic-provider-value-for-test'
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,{'MY_CHOSEN_KEY':secret}):
            root=Path(temp)
            for mode in ('native','compatible'):
                provider={'mode':mode,'model':'chosen','api_key_env':'MY_CHOSEN_KEY'}
                if mode=='native':provider['name']='anthropic'
                else:provider['base_url']='http://localhost/v1'
                name,env,values=providers.prepare(provider,root/mode)
                raw=(root/mode/'models.json').read_bytes()
                self.assertNotIn(secret.encode(),raw)
                self.assertIn(providers.SECRET_ENV.encode(),raw)
                self.assertEqual({providers.SECRET_ENV:secret},env)
                self.assertNotIn(secret.encode(),providers.sanitize(json.dumps({'text':secret}).encode(),values))

    def test_independent_checks_preserve_failure_timeout_and_partial_effects(self):
        spec=importlib.util.spec_from_file_location('pi_task_checks',DATA/'task_checks.py')
        checks=importlib.util.module_from_spec(spec);spec.loader.exec_module(checks)
        from harness_workbench.capture import minimal_environment
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            declared=[{'id':'failure','argv':[sys.executable,'-c',"from pathlib import Path; Path('effect').write_text('kept'); raise SystemExit(3)"]},
                {'id':'timeout','argv':[sys.executable,'-c','import time; time.sleep(30)'],'timeout':0.1}]
            result=checks.run(declared,root,minimal_environment(root/'isolated'))
            self.assertFalse(result['passed'])
            self.assertEqual(3,result['results'][0]['returncode'])
            self.assertEqual('timeout',result['results'][1]['termination_reason'])
            self.assertEqual('kept',(root/'effect').read_text())
            self.assertFalse(result['results'][1]['group_alive_after_cleanup'])
            for bad in ([],[{'id':'x','argv':[]}],[{'id':'x','argv':['echo'],'timeout':float('inf')}]):
                with self.assertRaises(ValueError):checks.validate(bad)

    @unittest.skipUnless(os.environ.get('HWB_PI_PROVIDER_LIVE_TEST')=='1','requires pinned Pi 1.4.0/Node runtime')
    def test_actual_pi_authentication_native_selection_and_redacted_capture(self):
        secret='synthetic-provider-auth-proof-839262'
        calls=[]
        reject=[False]
        rejected=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path,self.headers.get('Authorization'),body))
                if reject[0] or self.headers.get('Authorization')!='Bearer '+secret:
                    rejected.append(self.path)
                    self.send_error(401);return
                data=('data: '+json.dumps({'id':'fixture','object':'chat.completion.chunk','created':0,
                    'model':body['model'],'choices':[{'index':0,'delta':{'role':'assistant','content':'Selected credential: '+secret},'finish_reason':None}]})+'\n\n'+
                    'data: '+json.dumps({'id':'fixture','object':'chat.completion.chunk','created':0,'model':body['model'],
                    'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n').encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp)/'builder';self.command('init',root)
                result=self.command('configure',root,'--base-url',f'http://127.0.0.1:{server.server_port}/v1',
                    '--model','fixture-model','--api-key-env','MY_CHOSEN_KEY')
                self.assertEqual(0,result.returncode,result.stderr)
                result=self.command('run',root/'model_adapter_config.json','--pi',os.environ['HWB_PI_PROVIDER_LAUNCHER'],
                    '--timeout','45',env={'MY_CHOSEN_KEY':secret,'UNRELATED_PROVIDER_KEY':'must-not-inherit'})
                self.assertEqual(0,result.returncode,result.stdout[-4000:]+result.stderr)
                self.assertTrue(calls)
                self.assertEqual('fixture-model',calls[0][2]['model'])
                self.assertEqual('Bearer '+secret,calls[0][1])
                self.assertNotIn(secret,result.stdout+result.stderr)
                record=json.loads(result.stdout)
                self.assertTrue(record['verdict']['passed'])
                self.assertIn('[REDACTED]',record['pi']['stdout_jsonl'])
                self.assertEqual('MY_CHOSEN_KEY',record['configuration']['provider']['api_key_env'])
                for file in Path(record['workspace']['retained_root']).rglob('*'):
                    if file.is_file():self.assertNotIn(secret.encode(),file.read_bytes(),str(file))
                # Exercise a catalog provider through Pi's own implementation,
                # redirecting only its endpoint to this local protocol fixture.
                result=self.command('configure',root,'--provider','openrouter','--model','qwen/qwen-2.5-7b-instruct',
                    '--base-url',f'http://127.0.0.1:{server.server_port}/v1','--api-key-env','MY_CHOSEN_KEY',
                    '--config-name','native.json')
                self.assertEqual(0,result.returncode,result.stderr)
                result=self.command('run',root/'native.json','--pi',os.environ['HWB_PI_PROVIDER_LAUNCHER'],
                    '--timeout','45',env={'MY_CHOSEN_KEY':secret})
                self.assertEqual(0,result.returncode,result.stdout[-4000:]+result.stderr)
                self.assertEqual('qwen/qwen-2.5-7b-instruct',calls[-1][2]['model'])
                self.assertNotIn(secret,result.stdout+result.stderr)
                # A valid native capture must not hide a failed independent check.
                config=json.loads((root/'native.json').read_text())
                config['checks']=[{'id':'actual-tests','argv':[sys.executable,'-B','-m','unittest','-v']}]
                config['inputs'].append('task_checks.py')
                (root/'native.json').write_text(json.dumps(config))
                result=self.command('run',root/'native.json','--pi',os.environ['HWB_PI_PROVIDER_LAUNCHER'],
                    '--timeout','45',env={'MY_CHOSEN_KEY':secret})
                self.assertEqual(1,result.returncode,result.stdout[-4000:]+result.stderr)
                record=json.loads(result.stdout)
                self.assertTrue(record['verdict']['passed'])
                self.assertFalse(record['task_checks']['passed'])
                self.assertEqual(1,record['task_checks']['results'][0]['returncode'])
                # The check environment must not inherit selected provider credentials.
                config['checks']=[{'id':'credential-boundary','argv':[sys.executable,'-c',
                    "import os; assert 'HWB_SELECTED_PROVIDER_KEY' not in os.environ and 'MY_CHOSEN_KEY' not in os.environ"]}]
                (root/'native.json').write_text(json.dumps(config))
                result=self.command('run',root/'native.json','--pi',os.environ['HWB_PI_PROVIDER_LAUNCHER'],
                    '--timeout','45',env={'MY_CHOSEN_KEY':secret})
                self.assertEqual(0,result.returncode,result.stdout[-4000:]+result.stderr)
                self.assertTrue(json.loads(result.stdout)['task_checks']['passed'])
                reject[0]=True
                result=self.command('run',root/'native.json','--pi',os.environ['HWB_PI_PROVIDER_LAUNCHER'],
                    '--timeout','15',env={'MY_CHOSEN_KEY':secret})
                self.assertEqual(1,result.returncode,result.stdout[-4000:]+result.stderr)
                self.assertFalse(json.loads(result.stdout)['verdict']['passed'])
                self.assertTrue(rejected, 'must observe an actual rejected provider request')
                self.assertNotIn(secret,result.stdout+result.stderr)

        finally:
            server.shutdown();server.server_close();thread.join(5)


if __name__=='__main__':unittest.main()
