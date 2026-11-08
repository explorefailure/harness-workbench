"""Local native API protocol fixtures; no vendor accounts or inference claims."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT=Path(__file__).resolve().parents[1]
SECRET='synthetic-protocol-proof-853729'


def response_events(model, tool=None, arguments=None):
    if tool:
        args=json.dumps(arguments)
        item={'id':'fc_fixture','type':'function_call','call_id':'call_protocol','name':tool,'arguments':args,'status':'completed'}
        response={'id':'resp_fixture','object':'response','created_at':1,'status':'completed','model':model,'output':[item],
            'usage':{'input_tokens':8,'output_tokens':4,'total_tokens':12,'input_tokens_details':{'cached_tokens':0},
                     'output_tokens_details':{'reasoning_tokens':0}}}
        return [dict(e,sequence_number=i) for i,e in enumerate([
            {'type':'response.created','response':dict(response,status='in_progress',output=[])},
            {'type':'response.output_item.added','output_index':0,'item':dict(item,arguments='',status='in_progress')},
            {'type':'response.function_call_arguments.delta','output_index':0,'item_id':'fc_fixture','delta':args},
            {'type':'response.function_call_arguments.done','output_index':0,'item_id':'fc_fixture','arguments':args},
            {'type':'response.output_item.done','output_index':0,'item':item},
            {'type':'response.completed','response':response}])]
    message={'id':'msg_fixture','type':'message','status':'completed','role':'assistant',
             'content':[{'type':'output_text','text':'Fixture response.','annotations':[]}]}
    response={'id':'resp_fixture','object':'response','created_at':1,'status':'completed',
              'model':model,'output':[message],'usage':{'input_tokens':8,'output_tokens':4,'total_tokens':12,
              'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}}}
    pending=dict(response,status='in_progress',output=[])
    events=[{'type':'response.created','response':pending},
        {'type':'response.output_item.added','output_index':0,'item':dict(message,status='in_progress',content=[])},
        {'type':'response.content_part.added','output_index':0,'content_index':0,'item_id':message['id'],
         'part':{'type':'output_text','text':'','annotations':[]}},
        {'type':'response.output_text.delta','output_index':0,'content_index':0,'item_id':message['id'],'delta':'Fixture response.'},
        {'type':'response.output_text.done','output_index':0,'content_index':0,'item_id':message['id'],'text':'Fixture response.'},
        {'type':'response.output_item.done','output_index':0,'item':message},
        {'type':'response.completed','response':response}]
    return [dict(e,sequence_number=i) for i,e in enumerate(events)]


class ProtocolServer:
    def __enter__(self):
        self.calls=[];self.tool_sent=False;owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                self.connection.settimeout(5)
                count=int(self.headers.get('Content-Length','0'))
                if not 0<count<=1_048_576:self.send_error(413);return
                body=json.loads(self.rfile.read(count));path=urlsplit(self.path)
                if path.path=='/api/show':self.send_error(404);return
                key=(self.headers.get('Authorization','').removeprefix('Bearer ') or self.headers.get('x-api-key')
                     or self.headers.get('x-goog-api-key') or parse_qs(path.query).get('key',[''])[0])
                authed=key==SECRET
                model=body.get('model','fixture-model')
                definitions=[]
                for entry in body.get('tools',[]):
                    if 'function' in entry:definitions.append(entry['function'])
                    elif 'functionDeclarations' in entry:definitions.extend(entry['functionDeclarations'])
                    elif 'function_declarations' in entry:definitions.extend(entry['function_declarations'])
                    elif 'name' in entry:definitions.append(entry)
                selected=next((t for t in definitions if t.get('name') in ('bash','terminal')),None)
                tool=selected['name'] if selected and not owner.tool_sent else None
                arguments={'command':'pwd'}
                if tool:
                    owner.tool_sent=True
                    schema=selected.get('parameters',selected.get('input_schema',{}))
                    if 'description' in schema.get('properties',{}):arguments['description']='Run protocol tool control'
                if path.path.endswith('/messages'):
                    protocol='anthropic-messages'
                    events=[{'type':'message_start','message':{'id':'msg_fixture','type':'message','role':'assistant',
                        'content':[],'model':model,'stop_reason':None,'stop_sequence':None,
                        'usage':{'input_tokens':8,'output_tokens':0}}},
                        {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},
                        {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'Fixture response.'}},
                        {'type':'content_block_stop','index':0},
                        {'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':4}},
                        {'type':'message_stop'}]
                    if tool:
                        events[1]['content_block']={'type':'tool_use','id':'toolu_fixture','name':tool,'input':{}}
                        events[2]['delta']={'type':'input_json_delta','partial_json':json.dumps(arguments)}
                        events[4]['delta']['stop_reason']='tool_use'
                    payload=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events)
                elif path.path.endswith('/responses'):
                    protocol='openai-responses'
                    payload=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in response_events(model,tool,arguments))
                elif ':streamGenerateContent' in path.path:
                    protocol='google-generative-ai'
                    payload='data: '+json.dumps({'candidates':[{'index':0,'content':{'role':'model','parts':[{'functionCall':{'id':'call_protocol','name':tool,'args':arguments}}] if tool else [{'text':'Fixture response.'}]},
                        'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':8,'candidatesTokenCount':4,'totalTokenCount':12},
                        'modelVersion':'fixture-model'})+'\n\n'
                elif path.path.endswith('/chat/completions'):
                    protocol='openai-completions'
                    base={'id':'fixture','object':'chat.completion.chunk','created':1,'model':model}
                    payload=''.join('data: '+json.dumps(dict(base,choices=[{'index':0,'delta':delta,'finish_reason':reason}]))+'\n\n'
                        for delta,reason in [({'role':'assistant', **({'tool_calls':[{'index':0,'id':'call_protocol','type':'function','function':{'name':tool,'arguments':json.dumps(arguments)}}]} if tool else {'content':'Fixture response.'})},None),({},'tool_calls' if tool else 'stop')])+'data: [DONE]\n\n'
                else:self.send_error(404);return
                owner.calls.append({'path':path.path,'protocol':protocol,'authenticated':authed,'model':model,'tool_call':tool})
                if not authed:self.send_error(401);return
                data=payload.encode();self.send_response(200);self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base_url=f'http://127.0.0.1:{self.server.server_port}/v1'
        return self
    def __exit__(self,*_):
        self.server.shutdown();self.server.server_close();self.thread.join(5)


class NativeProtocolTests(unittest.TestCase):
    def command(self,name,*args):
        env=dict(os.environ,PROTOCOL_TEST_KEY=SECRET)
        python=os.environ.get('HWB_PROTOCOL_PYTHON',sys.executable)
        if os.environ.get('HWB_PROTOCOL_PYTHON'):env.pop('PYTHONPATH',None)
        else:env['PYTHONPATH']=str(ROOT/'src')
        return subprocess.run([python,'-m',f'harness_workbench._{name}_cli',*map(str,args)],
            capture_output=True,text=True,env=env,timeout=100)

    def exercise(self,name,selection,expected):
        with tempfile.TemporaryDirectory() as temp,ProtocolServer() as server:
            # A parent Git workspace exposed Hermes' implicit cwd canonicalization.
            subprocess.run(['git', 'init', '-q', temp], check=True, capture_output=True)
            root=Path(temp)/'builder';result=self.command(name,'init',root)
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)
            if name=='pi':
                result=self.command(name,'configure',root,'--model','fixture-model','--api',selection,
                    '--base-url',server.base_url,'--api-key-env','PROTOCOL_TEST_KEY')
                self.assertEqual(0,result.returncode,result.stdout+result.stderr)
                path=root/'model_adapter_config.json';runtime=['--pi',os.environ['HWB_PI_PROVIDER_LAUNCHER']]
            else:
                path=root/'adapter_config.json';config=json.loads(path.read_text())
                config['provider']={'mode':'native','name':selection,'model':'fixture-model',
                    'base_url':server.base_url,'api_key_env':'PROTOCOL_TEST_KEY'}
                path.write_text(json.dumps(config))
                runtime=['--opencode',os.environ['HWB_PROVIDER_OPENCODE']] if name=='opencode' else ['--runtime',os.environ['HWB_PROVIDER_HERMES']]
            result=self.command(name,'run',path,*runtime,'--timeout','45')
            if os.environ.get('HWB_PROTOCOL_EVIDENCE_DIR'):
                target=Path(tempfile.mkdtemp(prefix=name+'-'+selection+'-',dir=os.environ['HWB_PROTOCOL_EVIDENCE_DIR']))
                (target/'result.json').write_text(result.stdout)
                (target/'launcher.stderr').write_text(result.stderr)
                (target/'requests.json').write_text(json.dumps(server.calls,indent=2)+'\n')
                for file in root.rglob('*'):
                    if file.is_file() and not file.is_symlink():
                        copy=target/'workspace'/file.relative_to(root);copy.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copyfile(file,copy)
            self.assertEqual(0,result.returncode,result.stdout[-6000:]+result.stderr)
            record=json.loads(result.stdout)
            self.assertTrue(record['verdict']['passed'])
            self.assertTrue(server.calls,'must observe actual client request')
            self.assertTrue(all(c['authenticated'] for c in server.calls),server.calls)
            self.assertIn(server.calls[-1]['protocol'],expected)
            native_text=record['pi']['stdout_jsonl'] if name=='pi' else record['process']['stdout']['text']
            self.assertIn('Fixture response.',native_text)
            self.assertTrue(server.tool_sent,'fixture must request a native terminal tool')
            expected_cwd=str(Path(record['workspace']['retained_root'])/('workspace' if name=='pi' else 'fixture'))
            if name=='pi':
                events=[json.loads(line) for line in native_text.splitlines()]
                completed=[e for e in events if e.get('type')=='tool_execution_end' and e.get('toolName')=='bash']
                self.assertTrue(completed,'must retain actual native bash completion')
                self.assertFalse(completed[-1]['isError'])
                self.assertIn(expected_cwd,json.dumps(completed[-1]['result']))
            else:
                completed=[t for t in record['summary']['tools'] if t['tool'] in ('bash','terminal')]
                self.assertTrue(completed,'must retain actual native tool completion')
                code=completed[-1]['metadata']['exit'] if name=='opencode' else completed[-1]['exit_code']
                self.assertEqual(0,code)
                self.assertIn(expected_cwd,completed[-1]['output'])

            if name=='hermes':self.assertTrue(record['gateway_cleanup']['result']['passed'])
            self.assertNotIn(SECRET,result.stdout+result.stderr)
            for file in Path(record['workspace']['retained_root']).rglob('*'):
                if file.is_file():self.assertNotIn(SECRET.encode(),file.read_bytes(),str(file))

    @unittest.skipUnless(os.environ.get('HWB_NATIVE_PROTOCOL_TEST')=='1' or os.environ.get('HWB_PI_NATIVE_PROTOCOL_TEST')=='1', 'requires pinned runtime')
    def test_pi_protocols(self):
        for api in ('openai-completions','openai-responses','anthropic-messages','google-generative-ai'):
            with self.subTest(api=api):self.exercise('pi',api,{api})

    @unittest.skipUnless(os.environ.get('HWB_NATIVE_PROTOCOL_TEST')=='1' or os.environ.get('HWB_OPENCODE_NATIVE_PROTOCOL_TEST')=='1', 'requires pinned runtime')
    def test_opencode_native_sdks(self):
        for provider,protocol in [('openai','openai-responses'),('anthropic','anthropic-messages'),('google','google-generative-ai')]:
            with self.subTest(provider=provider):self.exercise('opencode',provider,{protocol})

    @unittest.skipUnless(os.environ.get('HWB_NATIVE_PROTOCOL_TEST')=='1' or os.environ.get('HWB_HERMES_NATIVE_PROTOCOL_TEST')=='1', 'requires pinned runtime')
    def test_hermes_native_providers(self):
        for provider,protocols in [('openai-api',{'openai-completions','openai-responses'}),('anthropic',{'anthropic-messages'})]:
            with self.subTest(provider=provider):self.exercise('hermes',provider,protocols)


if __name__=='__main__':unittest.main()
