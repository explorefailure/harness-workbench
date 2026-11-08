"""Authenticated compatible endpoints through actual OpenCode/Hermes clients."""
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def load(path):
    spec=importlib.util.spec_from_file_location('auth_fixture_'+path.parent.name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


class ContractTests(unittest.TestCase):
    def test_selected_credentials_and_config_are_explicit_for_both_adapters(self):
        for name,native in [('opencode',{'openrouter'}),('hermes',{'openrouter'})]:
            auth=load(ROOT/f'src/harness_workbench/{name}_builder/provider_auth.py')
            provider={'mode':'compatible','base_url':'http://localhost/v1','model':'chosen','api_key_env':'SELECTED_TEST_KEY'}
            auth.validate(provider,native)
            with patch.dict(os.environ,{},clear=True):
                with self.assertRaisesRegex(ValueError,'missing or empty'):auth.resolve(provider)
            with patch.dict(os.environ,{'SELECTED_TEST_KEY':'synthetic-selected-key','UNRELATED_KEY':'never-pass'}):
                env,values=auth.resolve(provider)
                self.assertEqual({'HWB_SELECTED_PROVIDER_KEY':'synthetic-selected-key'},env)
                self.assertEqual(['synthetic-selected-key'],values)
            for invalid in ({'api_key':'literal'}, {'api_key_env':'literal value'}, {'base_url':'http://u:p@localhost/v1'},
                    {'base_url':'http://localhost/v1?secret=x'},{'model':''},{'max_tokens':True}):
                with self.assertRaises(ValueError):auth.validate(dict(provider,**invalid),native)


class RealConnections(unittest.TestCase):
    def command(self,name,*args):
        return subprocess.run([sys.executable,'-m',f'harness_workbench._{name}_cli',*map(str,args)],
            env=dict(os.environ,PYTHONPATH=str(ROOT/'src'),SELECTED_TEST_KEY='synthetic-provider-proof-823146'),
            capture_output=True,text=True,timeout=100)

    def exercise(self,name,runtime_flag,runtime):
        secret='synthetic-provider-proof-823146';calls=[];reject=[False];rejected=[]
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path=='/api/show':self.send_error(404);return
                calls.append((self.path,self.headers.get('Authorization'),body))
                if reject[0] or self.headers.get('Authorization')!='Bearer '+secret:
                    rejected.append(self.path)
                    data=b'{"error":{"message":"invalid fixture credential","type":"authentication_error"}}'
                    self.send_response(401);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
                base={'id':'fixture','object':'chat.completion.chunk','created':0,'model':body['model']}
                chunks=[dict(base,choices=[{'index':0,'delta':{'role':'assistant','content':'Fixture response.'},'finish_reason':None}]),
                    dict(base,choices=[{'index':0,'delta':{},'finish_reason':'stop'}])]
                data=(''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
                if not body.get('stream'):
                    data=json.dumps({'id':'fixture','object':'chat.completion','created':0,'model':body['model'],
                        'choices':[{'index':0,'message':{'role':'assistant','content':'Fixture response.'},'finish_reason':'stop'}]}).encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json')
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp)/'builder';self.assertEqual(0,self.command(name,'init',root).returncode)
                path=root/'adapter_config.json';config=json.loads(path.read_text())
                config['provider']={'mode':'compatible','base_url':f'http://127.0.0.1:{server.server_port}/v1',
                    'model':'fixture-model','api_key_env':'SELECTED_TEST_KEY'}
                config['checks']=[{'id':'no-inherited-credentials','argv':[sys.executable,'-c',
                    "import os; assert not any(k in os.environ for k in ('SELECTED_TEST_KEY','HWB_SELECTED_PROVIDER_KEY','OPENAI_API_KEY','OPENROUTER_API_KEY'))"]}]

                if 'task_checks.py' not in config['inputs']:config['inputs'].append('task_checks.py')
                path.write_text(json.dumps(config))
                for mode in ('compatible','native'):
                    if mode=='native':config['provider'].update(mode='native',name='openrouter');path.write_text(json.dumps(config))
                    result=self.command(name,'run',path,runtime_flag,runtime,'--timeout','45')
                    self.assertEqual(0,result.returncode,result.stdout[-6000:]+result.stderr)
                    record=json.loads(result.stdout)
                    self.assertTrue(record['verdict']['passed']);self.assertTrue(record['task_checks']['passed'])
                    self.assertEqual('fixture-model',calls[-1][2]['model'])
                    self.assertEqual('Bearer '+secret,calls[-1][1])
                    self.assertNotIn(secret,result.stdout+result.stderr)
                    if name=='hermes':self.assertTrue(record['gateway_cleanup']['result']['passed'])
                    for file in Path(record['workspace']['retained_root']).rglob('*'):
                        if file.is_file():self.assertNotIn(secret.encode(),file.read_bytes(),str(file))
                config['checks']=[{'id':'failed-check','argv':[sys.executable,'-c','raise SystemExit(7)']}]
                path.write_text(json.dumps(config))
                result=self.command(name,'run',path,runtime_flag,runtime,'--timeout','45')
                self.assertEqual(1,result.returncode,result.stdout[-6000:]+result.stderr)
                record=json.loads(result.stdout)
                self.assertTrue(record['verdict']['passed']);self.assertFalse(record['task_checks']['passed'])
                reject[0]=True
                result=self.command(name,'run',path,runtime_flag,runtime,'--timeout','30')
                self.assertEqual(1,result.returncode,result.stdout[-6000:]+result.stderr)
                self.assertFalse(json.loads(result.stdout)['verdict']['passed'])
                self.assertTrue(rejected, 'must observe an actual rejected provider request')
                if name=='hermes':self.assertTrue(json.loads(result.stdout)['gateway_cleanup']['result']['passed'])
        finally:
            server.shutdown();server.server_close();thread.join(5)

    @unittest.skipUnless((os.environ.get('HWB_PROVIDER_CONNECTION_TEST')=='1' or os.environ.get('HWB_OPENCODE_PROVIDER_TEST')=='1'),'requires pinned runtime')
    def test_opencode_compatible_and_native_connections(self):
        self.exercise('opencode','--opencode',os.environ['HWB_PROVIDER_OPENCODE'])

    @unittest.skipUnless((os.environ.get('HWB_PROVIDER_CONNECTION_TEST')=='1' or os.environ.get('HWB_HERMES_PROVIDER_TEST')=='1'),'requires pinned runtime')
    def test_hermes_compatible_and_native_connections(self):
        self.exercise('hermes','--runtime',os.environ['HWB_PROVIDER_HERMES'])


if __name__=='__main__':unittest.main()
