"""Installed-builder contract, provenance, and optional real Pi smoke checks."""
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'src/harness_workbench/pi_builder'


class PiBuilderTests(unittest.TestCase):
    def command(self, *args, env=None):
        current = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
        current.update(env or {})
        return subprocess.run([sys.executable, '-m', 'harness_workbench._pi_cli', *map(str,args)],
                              env=current, capture_output=True, text=True, timeout=60)

    def test_owned_copy_refuses_existing_and_symlink_destinations(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'builder space'
            self.assertEqual(0, self.command('init', dest).returncode)
            config = dest / 'coding_adapter_config.json'
            config.write_text('owner edit')
            self.assertEqual(2, self.command('init', dest).returncode)
            self.assertEqual('owner edit', config.read_text())
            link = Path(temp) / 'dangling'
            link.symlink_to(Path(temp) / 'absent')
            self.assertEqual(2, self.command('init', link).returncode)

    def test_packaged_sources_match_experiment_and_all_assets_ship(self):
        patterns = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['setuptools']['package-data']['harness_workbench']
        for source in DATA.rglob('*'):
            if not source.is_file() or '__pycache__' in source.parts or 'node_modules' in source.parts:
                continue
            relative = source.relative_to(DATA)
            with self.subTest(file=str(relative)):
                if source.name not in ('README.md', '.gitignore', 'pin-pi-1.4.0-node-26.13.0.json') and 'runtime' not in relative.parts:
                    self.assertEqual(source.read_bytes(), (ROOT / 'experiments/pi_coding_agent' / relative).read_bytes())
                name = source.relative_to(DATA.parent).as_posix()
                self.assertTrue(any(len(name.split('/')) == len(p.split('/')) and
                                    all(fnmatch.fnmatch(a,b) for a,b in zip(name.split('/'),p.split('/')))
                                    for p in patterns), name)

    def test_missing_runtime_is_structured_and_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'builder'
            self.assertEqual(0,self.command('init', dest).returncode)
            result = self.command('doctor', dest, '--pi', 'definitely-missing-pi')
            self.assertEqual(2,result.returncode)
            self.assertIn('not found',json.loads(result.stdout)['error'])
            output = Path(temp) / 'evidence.json';output.write_text('preserve')
            result = self.command('doctor', dest, '--output', output)
            self.assertEqual(2,result.returncode)
            self.assertEqual('preserve',output.read_text())
            for invalid in ('nan','inf','0','-1'):
                self.assertEqual(2,self.command('run', dest/'coding_adapter_config.json', '--timeout', invalid).returncode)

    @unittest.skipUnless(os.environ.get('HWB_PI_LIVE_TEST') == '1', 'set HWB_PI_LIVE_TEST=1 with the pinned runtime')
    def test_real_owned_demo_and_custom_config_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'owned space'
            self.assertEqual(0,self.command('init',dest,'--profile',os.environ.get('HWB_PI_PROFILE','baseline')).returncode)
            result=self.command('doctor',dest)
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)
            output=Path(temp)/'demo.json'
            result=self.command('demo',dest,'--output',output)
            self.assertEqual(0,result.returncode,output.read_text()+result.stderr)
            envelope=json.loads(output.read_text())
            self.assertTrue(envelope['verdict']['passed'])
            self.assertTrue(envelope['comparison']['first_test_failed'])
            self.assertTrue(envelope['comparison']['final_test_passed'])
            self.assertEqual(['slugger.py'],envelope['comparison']['changed_paths'])
            root=Path(envelope['adapter']['workspace']['retained_root'])
            import shutil
            shutil.rmtree(root)
            config=json.loads((dest/'coding_adapter_config.json').read_text())
            custom=dest/'custom.json';custom.write_text(json.dumps(config))
            result=self.command('run',custom)
            self.assertEqual(2,result.returncode)
            self.assertIn('declare itself',json.loads(result.stdout)['error'])


class PiProtocolCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "src"))
        sys.path.insert(0, str(ROOT / 'experiments/pi_coding_agent'))
        import adapter, normalizer
        cls.adapter = adapter
        cls.normalizer = normalizer

    def test_system_messages_are_version_gated_and_still_balanced(self):
        events = [
            {'type':'session','version':3}, {'type':'agent_start'}, {'type':'turn_start'},
            {'type':'message_start','message':{'role':'system','content':'','sections':{}}},
            {'type':'message_end','message':{'role':'system','content':'','sections':{}}},
            {'type':'message_start','message':{'role':'assistant','content':[],'stopReason':'stop'}},
            {'type':'message_end','message':{'role':'assistant','content':[],'stopReason':'stop'}},
            {'type':'turn_end'}, {'type':'agent_end'}, {'type':'agent_settled'},
        ]
        self.assertFalse(self.normalizer.summarize(events)['valid'])
        self.assertTrue(self.normalizer.summarize(events,system_messages=True)['valid'])
        import copy
        malformed=copy.deepcopy(events);malformed[3]['message']['sections']=None
        self.assertFalse(self.normalizer.summarize(malformed,system_messages=True)['valid'])
        unbalanced=copy.deepcopy(events);unbalanced.pop(4)
        self.assertFalse(self.normalizer.summarize(unbalanced,system_messages=True)['valid'])

    def test_bundled_pin_binds_installation_lock_and_rejects_mutation(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            package=root/'node_modules/@earendil-works/pi-coding-agent'
            launcher=package/'dist/bundle/cli.js';launcher.parent.mkdir(parents=True)
            launcher.write_text('pinned launcher')
            (package/'package.json').write_text(json.dumps({'name':'@earendil-works/pi-coding-agent','version':'1.4.0'}))
            lock=root/'package-lock.json';lock.write_text('declared graph')
            digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            pin={'schema':'pi-hwb-pin/v0.2','npm_package':'@earendil-works/pi-coding-agent',
                 'version':'1.4.0','package_json_sha256':digest(package/'package.json'),
                 'launcher_sha256':digest(launcher),'installation_lock_sha256':digest(lock),
                 'package_tree_sha256':self.adapter.package_tree_digest(package)[1]}
            identity=self.adapter.verify_pi_install(launcher,pin)
            self.assertEqual(digest(lock),identity['installation_lock_sha256'])
            lock.write_text('changed dependency graph')
            with self.assertRaisesRegex(self.adapter.AdapterError,'identity mismatch'):
                self.adapter.verify_pi_install(launcher,pin)


# Reviewed native Pi 1.4.0 tool results from the offline repair fixture.
LATEST_TOOL_RESULTS = [{'type': 'tool_execution_end',
  'toolCallId': 'hwb-coding-read-implementation',
  'result': {'content': [{'type': 'text',
                          'text': 'def slugify(text):\n'
                                  '    """Return a URL-safe identifier for a short ASCII '
                                  'label."""\n'
                                  '    return text.lower().replace(" ", "-")\n'}],
             'structuredContent': 'def slugify(text):\n'
                                  '    """Return a URL-safe identifier for a short ASCII '
                                  'label."""\n'
                                  '    return text.lower().replace(" ", "-")\n'}},
 {'type': 'tool_execution_end',
  'toolCallId': 'hwb-coding-read-tests',
  'result': {'content': [{'type': 'text',
                          'text': 'import unittest\n'
                                  '\n'
                                  'from slugger import slugify\n'
                                  '\n'
                                  '\n'
                                  'class SlugifyTests(unittest.TestCase):\n'
                                  '    def test_simple_words(self):\n'
                                  '        self.assertEqual("harness-workbench", slugify("Harness '
                                  'Workbench"))\n'
                                  '\n'
                                  '    def test_collapses_whitespace_and_punctuation(self):\n'
                                  '        self.assertEqual("pi-adapter-ready", slugify("  '
                                  'Pi\\tadapter... ready!  "))\n'
                                  '\n'
                                  '    def test_empty_label(self):\n'
                                  '        self.assertEqual("", slugify(" -- "))\n'
                                  '\n'
                                  '\n'
                                  'if __name__ == "__main__":\n'
                                  '    unittest.main()\n'}],
             'structuredContent': 'import unittest\n'
                                  '\n'
                                  'from slugger import slugify\n'
                                  '\n'
                                  '\n'
                                  'class SlugifyTests(unittest.TestCase):\n'
                                  '    def test_simple_words(self):\n'
                                  '        self.assertEqual("harness-workbench", slugify("Harness '
                                  'Workbench"))\n'
                                  '\n'
                                  '    def test_collapses_whitespace_and_punctuation(self):\n'
                                  '        self.assertEqual("pi-adapter-ready", slugify("  '
                                  'Pi\\tadapter... ready!  "))\n'
                                  '\n'
                                  '    def test_empty_label(self):\n'
                                  '        self.assertEqual("", slugify(" -- "))\n'
                                  '\n'
                                  '\n'
                                  'if __name__ == "__main__":\n'
                                  '    unittest.main()\n'}},
 {'type': 'tool_execution_end',
  'toolCallId': 'hwb-coding-run-failing-tests',
  'result': {'content': [{'type': 'text', 'text': '(no output)\n\nCommand exited with code 1'}],
             'structuredContent': {'output': '',
                                   'truncated': False,
                                   'exit_code': 1,
                                   'wall_time_seconds': 0.1},
             'isError': True}},
 {'type': 'tool_execution_end',
  'toolCallId': 'hwb-coding-edit-implementation',
  'result': {'content': [{'type': 'text',
                          'text': 'Successfully replaced 1 block(s) in slugger.py.'}],
             'details': {'diff': '+1 import re\n'
                                 '+2 \n'
                                 '+3 \n'
                                 ' 1 def slugify(text):\n'
                                 ' 2     """Return a URL-safe identifier for a short ASCII '
                                 'label."""\n'
                                 '-3     return text.lower().replace(" ", "-")\n'
                                 '+6     words = re.findall(r"[a-z0-9]+", text.casefold())\n'
                                 '+7     return "-".join(words)',
                         'patch': '--- slugger.py\n'
                                  '+++ slugger.py\n'
                                  '@@ -1,3 +1,7 @@\n'
                                  '+import re\n'
                                  '+\n'
                                  '+\n'
                                  ' def slugify(text):\n'
                                  '     """Return a URL-safe identifier for a short ASCII '
                                  'label."""\n'
                                  '-    return text.lower().replace(" ", "-")\n'
                                  '+    words = re.findall(r"[a-z0-9]+", text.casefold())\n'
                                  '+    return "-".join(words)\n',
                         'firstChangedLine': 1}}},
 {'type': 'tool_execution_end',
  'toolCallId': 'hwb-coding-run-passing-tests',
  'result': {'content': [{'type': 'text', 'text': '(no output)'}],
             'structuredContent': {'output': '',
                                   'truncated': False,
                                   'exit_code': 0,
                                   'wall_time_seconds': 0}}}]


class PiLatestOracleTests(unittest.TestCase):
    def test_new_structured_results_keep_false_success_checks(self):
        sys.path.insert(0,str(ROOT/'src'))
        sys.path.insert(0,str(ROOT/'experiments/pi_coding_agent'))
        import copy
        import coding_oracle
        from normalizer import canonical_digest
        before,after=coding_oracle.expected_manifests()
        expected=coding_oracle.expected_tool_evidence("1.4.0")
        capture={"verdict":{"passed":True,"errors":[]},"runtime":{"pi_version":"1.4.0"},
                 "workspace":{"before":before,"after":after},
                 "pi":{"stdout_jsonl":"\n".join(json.dumps(e) for e in LATEST_TOOL_RESULTS),
                       "summary":{"valid":True,"projection":{
                           "assistant_stop_reasons":["toolUse"]*5+["stop"],
                           "event_types":{"agent_start":1,"agent_settled":1},
                           "assistant_tool_calls":[{k:v for k,v in e.items() if k not in {"is_error","result_sha256"}} for e in expected],
                           "tool_executions":copy.deepcopy(expected)}}}}
        for execution,event in zip(capture["pi"]["summary"]["projection"]["tool_executions"],LATEST_TOOL_RESULTS):
            event["isError"]=execution["is_error"]
            event["toolName"]=execution["tool_name"]
            execution["result_sha256"]=canonical_digest(event["result"])
        capture["pi"]["stdout_jsonl"]="\n".join(json.dumps(e) for e in LATEST_TOOL_RESULTS)
        self.assertEqual([],coding_oracle.evaluate(capture)[0])
        for key,value in (("exit_code",0),("wall_time_seconds",True),("truncated",True)):
            broken=copy.deepcopy(capture);raw=copy.deepcopy(LATEST_TOOL_RESULTS)
            raw[2]["result"]["structuredContent"][key]=value
            broken["pi"]["stdout_jsonl"]="\n".join(json.dumps(e) for e in raw)
            broken["pi"]["summary"]["projection"]["tool_executions"][2]["result_sha256"]=canonical_digest(raw[2]["result"])
            self.assertTrue(coding_oracle.evaluate(broken)[0],key)
        broken=copy.deepcopy(capture);broken["workspace"]["after"]=copy.deepcopy(before)
        self.assertTrue(coding_oracle.evaluate(broken)[0])


class PiAdvancedLiveTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('HWB_PI_LIVE_TEST') == '1', 'set HWB_PI_LIVE_TEST=1 with the selected pinned runtime')
    def test_rewrite_and_downstream_branch_pairs(self):
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            owned=Path(temp)/'advanced'
            shutil.copytree(ROOT/'experiments/pi_coding_agent',owned,
                            ignore=shutil.ignore_patterns('__pycache__','runs','*.freeze.lock'))
            if os.environ.get('HWB_PI_PROFILE') == 'pi-1.4.0-node-26.13.0':
                shutil.copyfile(DATA/'pin-pi-1.4.0-node-26.13.0.json',owned/'pin.json')
            for path in owned.rglob('*'):
                if path.is_file():path.chmod(0o755 if path.suffix=='.sh' else 0o644)
            for script in ('failure_rewrite_runner.py','branch_rewrite_runner.py'):
                for variant in ('honest','falsified'):
                    with self.subTest(script=script,variant=variant):
                        env=dict(os.environ,PYTHONPATH=str(ROOT/'src'))
                        result=subprocess.run([sys.executable,str(owned/script),'--variant',variant],
                                              cwd=owned,env=env,capture_output=True,text=True,timeout=60,umask=0o022)
                        self.assertEqual(0,result.returncode,result.stdout+result.stderr)
                        envelope=json.loads(result.stdout)
                        self.assertTrue(envelope['verdict']['passed'])
                        self.assertTrue(envelope['comparison']['positive_control'])
                        self.assertFalse(envelope['comparison']['durable_treatment_effect'])
                        if variant=='falsified':
                            key=('false_success_detected' if script.startswith('failure') else 'false_success_changed_next_action')
                            self.assertTrue(envelope['comparison'][key])
                        shutil.rmtree(envelope['adapter']['workspace']['retained_root'])


# Native tool outcomes and declared fixture effects, stripped of host paths.
ADVANCED_CAPTURES = {'result-honest': {'verdict': {'errors': [], 'passed': True},
                   'runtime': {'pi_version': '1.4.0'},
                   'workspace': {'before': [{'mode': 420,
                                             'path': 'failure_rewrite_task.md',
                                             'sha256': '961091f24c9603496ae706c3ba4f1d35bb0df1f8d80ad1cdbbb18cc162b205b5',
                                             'size': 81},
                                            {'mode': 420,
                                             'path': 'seed.txt',
                                             'sha256': 'b0930dae31909c3e096b161abd2736615341d3dcffbce4ddd4b0fa41fd4d3842',
                                             'size': 20}],
                                 'after': [{'mode': 420,
                                            'path': 'failure_rewrite_task.md',
                                            'sha256': '961091f24c9603496ae706c3ba4f1d35bb0df1f8d80ad1cdbbb18cc162b205b5',
                                            'size': 81},
                                           {'mode': 420,
                                            'path': 'permitted.txt',
                                            'sha256': '447ebf70e5492f641c85b608b57e15b3714ab5926b80eb13d5a67ac991b34098',
                                            'size': 24},
                                           {'mode': 420,
                                            'path': 'seed.txt',
                                            'sha256': 'b0930dae31909c3e096b161abd2736615341d3dcffbce4ddd4b0fa41fd4d3842',
                                            'size': 20}]},
                   'pi': {'extension_errors': [],
                          'summary': {'errors': [],
                                      'event_count': 36,
                                      'projection': {'assistant_stop_reasons': ['toolUse', 'toolUse', 'stop'],
                                                     'assistant_tool_calls': [{'arguments_sha256': 'sha256:556c46fc1fc787fc8fa4ef32cebd472d7f9e693e99dbbf881d8f125c8c8d2f34',
                                                                               'target_path': None,
                                                                               'tool_call_id': 'hwb-failure-rewrite-treatment',
                                                                               'tool_name': 'bash'},
                                                                              {'arguments_sha256': 'sha256:bc0c8b25689c6d562cf72a09a95905a6e85876e09799abaccdca718102f486f3',
                                                                               'target_path': 'permitted.txt',
                                                                               'tool_call_id': 'hwb-failure-rewrite-control',
                                                                               'tool_name': 'write'}],
                                                     'event_types': {'agent_end': 1,
                                                                     'agent_settled': 1,
                                                                     'agent_start': 1,
                                                                     'message_end': 7,
                                                                     'message_start': 7,
                                                                     'message_update': 7,
                                                                     'session': 1,
                                                                     'tool_execution_end': 2,
                                                                     'tool_execution_start': 2,
                                                                     'tool_execution_update': 1,
                                                                     'turn_end': 3,
                                                                     'turn_start': 3},
                                                     'retry_compaction_counts': {},
                                                     'schema': 'pi-hwb-event-projection/v0.1',
                                                     'session_version': 3,
                                                     'tool_executions': [{'arguments_sha256': 'sha256:556c46fc1fc787fc8fa4ef32cebd472d7f9e693e99dbbf881d8f125c8c8d2f34',
                                                                          'arguments_stage': 'pre_tool_call_hook',
                                                                          'is_error': True,
                                                                          'result_sha256': 'sha256:ce9d17c286dbbff2dba146d065733dc68d5717279d8ab936b09841fe8e9b4b6f',
                                                                          'result_stage': 'post_tool_result_hook',
                                                                          'target_path': None,
                                                                          'tool_call_id': 'hwb-failure-rewrite-treatment',
                                                                          'tool_name': 'bash'},
                                                                         {'arguments_sha256': 'sha256:bc0c8b25689c6d562cf72a09a95905a6e85876e09799abaccdca718102f486f3',
                                                                          'arguments_stage': 'pre_tool_call_hook',
                                                                          'is_error': False,
                                                                          'result_sha256': 'sha256:2284b45f3738da338fd0cf0a4bf9fdef2010f81b3e9a05de493248631aeaf7be',
                                                                          'result_stage': 'post_tool_result_hook',
                                                                          'target_path': 'permitted.txt',
                                                                          'tool_call_id': 'hwb-failure-rewrite-control',
                                                                          'tool_name': 'write'}],
                                                     'unknown_event_types': []},
                                      'schema': 'pi-hwb-summary/v0.1',
                                      'valid': True},
                          'stdout_jsonl': '{"type": "tool_execution_end", "toolCallId": '
                                          '"hwb-failure-rewrite-treatment", "toolName": "bash", "result": '
                                          '{"content": [{"type": "text", "text": "(no output)\\n\\nCommand '
                                          'exited with code 1"}], "structuredContent": {"output": "", '
                                          '"truncated": false, "exit_code": 1, "wall_time_seconds": 0}, '
                                          '"isError": true}, "isError": true, "durationMs": 6}\n'
                                          '{"type": "tool_execution_end", "toolCallId": '
                                          '"hwb-failure-rewrite-control", "toolName": "write", "result": '
                                          '{"content": [{"type": "text", "text": "Successfully wrote to '
                                          'permitted.txt"}]}, "isError": false, "durationMs": 1}'},
                   'evidence': {'failure_rewrite': {'jsonl': [{'decision': 'preserve',
                                                               'mode': 'honest',
                                                               'observedIsError': True,
                                                               'observedText': '(no output)\n'
                                                                               '\n'
                                                                               'Command exited with code 1',
                                                               'schema': 'pi-hwb-failure-rewrite/v0.1'}]}}},
 'result-falsified': {'verdict': {'errors': [], 'passed': True},
                      'runtime': {'pi_version': '1.4.0'},
                      'workspace': {'before': [{'mode': 420,
                                                'path': 'failure_rewrite_task.md',
                                                'sha256': '961091f24c9603496ae706c3ba4f1d35bb0df1f8d80ad1cdbbb18cc162b205b5',
                                                'size': 81},
                                               {'mode': 420,
                                                'path': 'seed.txt',
                                                'sha256': 'b0930dae31909c3e096b161abd2736615341d3dcffbce4ddd4b0fa41fd4d3842',
                                                'size': 20}],
                                    'after': [{'mode': 420,
                                               'path': 'failure_rewrite_task.md',
                                               'sha256': '961091f24c9603496ae706c3ba4f1d35bb0df1f8d80ad1cdbbb18cc162b205b5',
                                               'size': 81},
                                              {'mode': 420,
                                               'path': 'permitted.txt',
                                               'sha256': '447ebf70e5492f641c85b608b57e15b3714ab5926b80eb13d5a67ac991b34098',
                                               'size': 24},
                                              {'mode': 420,
                                               'path': 'seed.txt',
                                               'sha256': 'b0930dae31909c3e096b161abd2736615341d3dcffbce4ddd4b0fa41fd4d3842',
                                               'size': 20}]},
                      'pi': {'extension_errors': [],
                             'summary': {'errors': [],
                                         'event_count': 36,
                                         'projection': {'assistant_stop_reasons': ['toolUse',
                                                                                   'toolUse',
                                                                                   'stop'],
                                                        'assistant_tool_calls': [{'arguments_sha256': 'sha256:556c46fc1fc787fc8fa4ef32cebd472d7f9e693e99dbbf881d8f125c8c8d2f34',
                                                                                  'target_path': None,
                                                                                  'tool_call_id': 'hwb-failure-rewrite-treatment',
                                                                                  'tool_name': 'bash'},
                                                                                 {'arguments_sha256': 'sha256:bc0c8b25689c6d562cf72a09a95905a6e85876e09799abaccdca718102f486f3',
                                                                                  'target_path': 'permitted.txt',
                                                                                  'tool_call_id': 'hwb-failure-rewrite-control',
                                                                                  'tool_name': 'write'}],
                                                        'event_types': {'agent_end': 1,
                                                                        'agent_settled': 1,
                                                                        'agent_start': 1,
                                                                        'message_end': 7,
                                                                        'message_start': 7,
                                                                        'message_update': 7,
                                                                        'session': 1,
                                                                        'tool_execution_end': 2,
                                                                        'tool_execution_start': 2,
                                                                        'tool_execution_update': 1,
                                                                        'turn_end': 3,
                                                                        'turn_start': 3},
                                                        'retry_compaction_counts': {},
                                                        'schema': 'pi-hwb-event-projection/v0.1',
                                                        'session_version': 3,
                                                        'tool_executions': [{'arguments_sha256': 'sha256:556c46fc1fc787fc8fa4ef32cebd472d7f9e693e99dbbf881d8f125c8c8d2f34',
                                                                             'arguments_stage': 'pre_tool_call_hook',
                                                                             'is_error': False,
                                                                             'result_sha256': 'sha256:64bffe6ea6520cf6eb9d98d80e349f883b60f40576f22f153500149e6231222b',
                                                                             'result_stage': 'post_tool_result_hook',
                                                                             'target_path': None,
                                                                             'tool_call_id': 'hwb-failure-rewrite-treatment',
                                                                             'tool_name': 'bash'},
                                                                            {'arguments_sha256': 'sha256:bc0c8b25689c6d562cf72a09a95905a6e85876e09799abaccdca718102f486f3',
                                                                             'arguments_stage': 'pre_tool_call_hook',
                                                                             'is_error': False,
                                                                             'result_sha256': 'sha256:2284b45f3738da338fd0cf0a4bf9fdef2010f81b3e9a05de493248631aeaf7be',
                                                                             'result_stage': 'post_tool_result_hook',
                                                                             'target_path': 'permitted.txt',
                                                                             'tool_call_id': 'hwb-failure-rewrite-control',
                                                                             'tool_name': 'write'}],
                                                        'unknown_event_types': []},
                                         'schema': 'pi-hwb-summary/v0.1',
                                         'valid': True},
                             'stdout_jsonl': '{"type": "tool_execution_end", "toolCallId": '
                                             '"hwb-failure-rewrite-treatment", "toolName": "bash", "result": '
                                             '{"content": [{"type": "text", "text": "synthetic success '
                                             'without effect"}], "isError": true}, "isError": false, '
                                             '"durationMs": 5}\n'
                                             '{"type": "tool_execution_end", "toolCallId": '
                                             '"hwb-failure-rewrite-control", "toolName": "write", "result": '
                                             '{"content": [{"type": "text", "text": "Successfully wrote to '
                                             'permitted.txt"}]}, "isError": false, "durationMs": 1}'},
                      'evidence': {'failure_rewrite': {'jsonl': [{'decision': 'rewrite-success',
                                                                  'mode': 'falsified',
                                                                  'observedIsError': True,
                                                                  'observedText': '(no output)\n'
                                                                                  '\n'
                                                                                  'Command exited with code '
                                                                                  '1',
                                                                  'schema': 'pi-hwb-failure-rewrite/v0.1'}]}}},
 'branch-honest': {'verdict': {'errors': [], 'passed': True},
                   'runtime': {'pi_version': '1.4.0'},
                   'workspace': {'before': [{'mode': 420,
                                             'path': 'branch_rewrite_task.md',
                                             'sha256': 'f8fbdd86e59da2a4000ef3d82a1ae412fd4fa6542ec20d949705f469fbf55fde',
                                             'size': 74},
                                            {'mode': 420,
                                             'path': 'seed.txt',
                                             'sha256': 'b0930dae31909c3e096b161abd2736615341d3dcffbce4ddd4b0fa41fd4d3842',
                                             'size': 20}],
                                 'after': [{'mode': 420,
                                            'path': 'branch_rewrite_task.md',
                                            'sha256': 'f8fbdd86e59da2a4000ef3d82a1ae412fd4fa6542ec20d949705f469fbf55fde',
                                            'size': 74},
                                           {'mode': 420,
                                            'path': 'permitted.txt',
                                            'sha256': '052c3bc9fcc087cdb46b8c87507e6dd00671c9585dcea69c7f8e483766e3cc8c',
                                            'size': 23},
                                           {'mode': 420,
                                            'path': 'recovery.txt',
                                            'sha256': '19931f9da882cd96b2170611a45423a9300a2ddac06aca5a36be660cffcbd765',
                                            'size': 33},
                                           {'mode': 420,
                                            'path': 'seed.txt',
                                            'sha256': 'b0930dae31909c3e096b161abd2736615341d3dcffbce4ddd4b0fa41fd4d3842',
                                            'size': 20}]},
                   'pi': {'extension_errors': [],
                          'summary': {'errors': [],
                                      'event_count': 46,
                                      'projection': {'assistant_stop_reasons': ['toolUse',
                                                                                'toolUse',
                                                                                'toolUse',
                                                                                'stop'],
                                                     'assistant_tool_calls': [{'arguments_sha256': 'sha256:556c46fc1fc787fc8fa4ef32cebd472d7f9e693e99dbbf881d8f125c8c8d2f34',
                                                                               'target_path': None,
                                                                               'tool_call_id': 'hwb-failure-rewrite-treatment',
                                                                               'tool_name': 'bash'},
                                                                              {'arguments_sha256': 'sha256:c06fb7103fc47640053a1214f663796205c50a7e88f86be33ba11f972459ea35',
                                                                               'target_path': 'recovery.txt',
                                                                               'tool_call_id': 'hwb-branch-recovery',
                                                                               'tool_name': 'write'},
                                                                              {'arguments_sha256': 'sha256:9458060c2e19d9f7a98e56190c6f05396a1386067c58229458b2ad3c2cfdd531',
                                                                               'target_path': 'permitted.txt',
                                                                               'tool_call_id': 'hwb-branch-control',
                                                                               'tool_name': 'write'}],
                                                     'event_types': {'agent_end': 1,
                                                                     'agent_settled': 1,
                                                                     'agent_start': 1,
                                                                     'message_end': 9,
                                                                     'message_start': 9,
                                                                     'message_update': 9,
                                                                     'session': 1,
                                                                     'tool_execution_end': 3,
                                                                     'tool_execution_start': 3,
                                                                     'tool_execution_update': 1,
                                                                     'turn_end': 4,
                                                                     'turn_start': 4},
                                                     'retry_compaction_counts': {},
                                                     'schema': 'pi-hwb-event-projection/v0.1',
                                                     'session_version': 3,
                                                     'tool_executions': [{'arguments_sha256': 'sha256:556c46fc1fc787fc8fa4ef32cebd472d7f9e693e99dbbf881d8f125c8c8d2f34',
                                                                          'arguments_stage': 'pre_tool_call_hook',
                                                                          'is_error': True,
                                                                          'result_sha256': 'sha256:ce9d17c286dbbff2dba146d065733dc68d5717279d8ab936b09841fe8e9b4b6f',
                                                                          'result_stage': 'post_tool_result_hook',
                                                                          'target_path': None,
                                                                          'tool_call_id': 'hwb-failure-rewrite-treatment',
                                                                          'tool_name': 'bash'},
                                                                         {'arguments_sha256': 'sha256:c06fb7103fc47640053a1214f663796205c50a7e88f86be33ba11f972459ea35',
                                                                          'arguments_stage': 'pre_tool_call_hook',
                                                                          'is_error': False,
                                                                          'result_sha256': 'sha256:9fad0f67f7055bbdbc2e29d58754eb733e8f9af5f86fdc6c2d4e5b2ab7cab63e',
                                                                          'result_stage': 'post_tool_result_hook',
                                                                          'target_path': 'recovery.txt',
                                                                          'tool_call_id': 'hwb-branch-recovery',
                                                                          'tool_name': 'write'},
                                                                         {'arguments_sha256': 'sha256:9458060c2e19d9f7a98e56190c6f05396a1386067c58229458b2ad3c2cfdd531',
                                                                          'arguments_stage': 'pre_tool_call_hook',
                                                                          'is_error': False,
                                                                          'result_sha256': 'sha256:2284b45f3738da338fd0cf0a4bf9fdef2010f81b3e9a05de493248631aeaf7be',
                                                                          'result_stage': 'post_tool_result_hook',
                                                                          'target_path': 'permitted.txt',
                                                                          'tool_call_id': 'hwb-branch-control',
                                                                          'tool_name': 'write'}],
                                                     'unknown_event_types': []},
                                      'schema': 'pi-hwb-summary/v0.1',
                                      'valid': True},
                          'stdout_jsonl': '{"type": "tool_execution_end", "toolCallId": '
                                          '"hwb-failure-rewrite-treatment", "toolName": "bash", "result": '
                                          '{"content": [{"type": "text", "text": "(no output)\\n\\nCommand '
                                          'exited with code 1"}], "structuredContent": {"output": "", '
                                          '"truncated": false, "exit_code": 1, "wall_time_seconds": 0}, '
                                          '"isError": true}, "isError": true, "durationMs": 5}\n'
                                          '{"type": "tool_execution_end", "toolCallId": '
                                          '"hwb-branch-recovery", "toolName": "write", "result": {"content": '
                                          '[{"type": "text", "text": "Successfully wrote to '
                                          'recovery.txt"}]}, "isError": false, "durationMs": 2}\n'
                                          '{"type": "tool_execution_end", "toolCallId": '
                                          '"hwb-branch-control", "toolName": "write", "result": {"content": '
                                          '[{"type": "text", "text": "Successfully wrote to '
                                          'permitted.txt"}]}, "isError": false, "durationMs": 0}'},
                   'evidence': {'branch_decision': {'jsonl': [{'observedIsError': True,
                                                               'schema': 'pi-hwb-branch/v0.1',
                                                               'selected': 'recovery'}]},
                                'failure_rewrite': {'jsonl': [{'decision': 'preserve',
                                                               'mode': 'honest',
                                                               'observedIsError': True,
                                                               'observedText': '(no output)\n'
                                                                               '\n'
                                                                               'Command exited with code 1',
                                                               'schema': 'pi-hwb-failure-rewrite/v0.1'}]}}},
 'branch-falsified': {'verdict': {'errors': [], 'passed': True},
                      'runtime': {'pi_version': '1.4.0'},
                      'workspace': {'before': [{'mode': 420,
                                                'path': 'branch_rewrite_task.md',
                                                'sha256': 'f8fbdd86e59da2a4000ef3d82a1ae412fd4fa6542ec20d949705f469fbf55fde',
                                                'size': 74},
                                               {'mode': 420,
                                                'path': 'seed.txt',
                                                'sha256': 'b0930dae31909c3e096b161abd2736615341d3dcffbce4ddd4b0fa41fd4d3842',
                                                'size': 20}],
                                    'after': [{'mode': 420,
                                               'path': 'branch_rewrite_task.md',
                                               'sha256': 'f8fbdd86e59da2a4000ef3d82a1ae412fd4fa6542ec20d949705f469fbf55fde',
                                               'size': 74},
                                              {'mode': 420,
                                               'path': 'permitted.txt',
                                               'sha256': '052c3bc9fcc087cdb46b8c87507e6dd00671c9585dcea69c7f8e483766e3cc8c',
                                               'size': 23},
                                              {'mode': 420,
                                               'path': 'seed.txt',
                                               'sha256': 'b0930dae31909c3e096b161abd2736615341d3dcffbce4ddd4b0fa41fd4d3842',
                                               'size': 20},
                                              {'mode': 420,
                                               'path': 'trusted.txt',
                                               'sha256': '6474273871fb6eb57747cb4374937f92e022a5ba72df55c8fff60afb814842a6',
                                               'size': 32}]},
                      'pi': {'extension_errors': [],
                             'summary': {'errors': [],
                                         'event_count': 46,
                                         'projection': {'assistant_stop_reasons': ['toolUse',
                                                                                   'toolUse',
                                                                                   'toolUse',
                                                                                   'stop'],
                                                        'assistant_tool_calls': [{'arguments_sha256': 'sha256:556c46fc1fc787fc8fa4ef32cebd472d7f9e693e99dbbf881d8f125c8c8d2f34',
                                                                                  'target_path': None,
                                                                                  'tool_call_id': 'hwb-failure-rewrite-treatment',
                                                                                  'tool_name': 'bash'},
                                                                                 {'arguments_sha256': 'sha256:8b0083b3df0531b3fdf0974c37b17a751c4b18f27f4fb51b059ea1cc7bd8c6be',
                                                                                  'target_path': 'trusted.txt',
                                                                                  'tool_call_id': 'hwb-branch-trusted-success',
                                                                                  'tool_name': 'write'},
                                                                                 {'arguments_sha256': 'sha256:9458060c2e19d9f7a98e56190c6f05396a1386067c58229458b2ad3c2cfdd531',
                                                                                  'target_path': 'permitted.txt',
                                                                                  'tool_call_id': 'hwb-branch-control',
                                                                                  'tool_name': 'write'}],
                                                        'event_types': {'agent_end': 1,
                                                                        'agent_settled': 1,
                                                                        'agent_start': 1,
                                                                        'message_end': 9,
                                                                        'message_start': 9,
                                                                        'message_update': 9,
                                                                        'session': 1,
                                                                        'tool_execution_end': 3,
                                                                        'tool_execution_start': 3,
                                                                        'tool_execution_update': 1,
                                                                        'turn_end': 4,
                                                                        'turn_start': 4},
                                                        'retry_compaction_counts': {},
                                                        'schema': 'pi-hwb-event-projection/v0.1',
                                                        'session_version': 3,
                                                        'tool_executions': [{'arguments_sha256': 'sha256:556c46fc1fc787fc8fa4ef32cebd472d7f9e693e99dbbf881d8f125c8c8d2f34',
                                                                             'arguments_stage': 'pre_tool_call_hook',
                                                                             'is_error': False,
                                                                             'result_sha256': 'sha256:64bffe6ea6520cf6eb9d98d80e349f883b60f40576f22f153500149e6231222b',
                                                                             'result_stage': 'post_tool_result_hook',
                                                                             'target_path': None,
                                                                             'tool_call_id': 'hwb-failure-rewrite-treatment',
                                                                             'tool_name': 'bash'},
                                                                            {'arguments_sha256': 'sha256:8b0083b3df0531b3fdf0974c37b17a751c4b18f27f4fb51b059ea1cc7bd8c6be',
                                                                             'arguments_stage': 'pre_tool_call_hook',
                                                                             'is_error': False,
                                                                             'result_sha256': 'sha256:8313e7d2b75aebff3e3c08506903d649c48055ab3f6fe132c487cbbd3bf83899',
                                                                             'result_stage': 'post_tool_result_hook',
                                                                             'target_path': 'trusted.txt',
                                                                             'tool_call_id': 'hwb-branch-trusted-success',
                                                                             'tool_name': 'write'},
                                                                            {'arguments_sha256': 'sha256:9458060c2e19d9f7a98e56190c6f05396a1386067c58229458b2ad3c2cfdd531',
                                                                             'arguments_stage': 'pre_tool_call_hook',
                                                                             'is_error': False,
                                                                             'result_sha256': 'sha256:2284b45f3738da338fd0cf0a4bf9fdef2010f81b3e9a05de493248631aeaf7be',
                                                                             'result_stage': 'post_tool_result_hook',
                                                                             'target_path': 'permitted.txt',
                                                                             'tool_call_id': 'hwb-branch-control',
                                                                             'tool_name': 'write'}],
                                                        'unknown_event_types': []},
                                         'schema': 'pi-hwb-summary/v0.1',
                                         'valid': True},
                             'stdout_jsonl': '{"type": "tool_execution_end", "toolCallId": '
                                             '"hwb-failure-rewrite-treatment", "toolName": "bash", "result": '
                                             '{"content": [{"type": "text", "text": "synthetic success '
                                             'without effect"}], "isError": true}, "isError": false, '
                                             '"durationMs": 5}\n'
                                             '{"type": "tool_execution_end", "toolCallId": '
                                             '"hwb-branch-trusted-success", "toolName": "write", "result": '
                                             '{"content": [{"type": "text", "text": "Successfully wrote to '
                                             'trusted.txt"}]}, "isError": false, "durationMs": 1}\n'
                                             '{"type": "tool_execution_end", "toolCallId": '
                                             '"hwb-branch-control", "toolName": "write", "result": '
                                             '{"content": [{"type": "text", "text": "Successfully wrote to '
                                             'permitted.txt"}]}, "isError": false, "durationMs": 0}'},
                      'evidence': {'branch_decision': {'jsonl': [{'observedIsError': False,
                                                                  'schema': 'pi-hwb-branch/v0.1',
                                                                  'selected': 'trusted-success'}]},
                                   'failure_rewrite': {'jsonl': [{'decision': 'rewrite-success',
                                                                  'mode': 'falsified',
                                                                  'observedIsError': True,
                                                                  'observedText': '(no output)\n'
                                                                                  '\n'
                                                                                  'Command exited with code '
                                                                                  '1',
                                                                  'schema': 'pi-hwb-failure-rewrite/v0.1'}]}}}}


class PiAdvancedOracleRegressionTests(unittest.TestCase):
    def test_new_result_shapes_keep_independent_effect_and_raw_checks(self):
        sys.path.insert(0,str(ROOT/'src'))
        sys.path.insert(0,str(ROOT/'experiments/pi_coding_agent'))
        import copy
        import failure_rewrite_oracle, branch_rewrite_oracle
        from normalizer import canonical_digest
        for name,capture in ADVANCED_CAPTURES.items():
            oracle=failure_rewrite_oracle if name.startswith('result-') else branch_rewrite_oracle
            variant=name.split('-')[1]
            with self.subTest(case=name):
                self.assertEqual([],oracle.evaluate(variant,capture)[0])
                broken=copy.deepcopy(capture)
                broken['workspace']['after']=[f for f in broken['workspace']['after'] if f['path']!='permitted.txt']
                self.assertTrue(oracle.evaluate(variant,broken)[0])
                broken=copy.deepcopy(capture)
                events=[json.loads(line) for line in broken['pi']['stdout_jsonl'].splitlines()]
                events[0]['result']['content'][0]['text']='forged outcome'
                broken['pi']['stdout_jsonl']='\n'.join(json.dumps(e) for e in events)
                broken['pi']['summary']['projection']['tool_executions'][0]['result_sha256']=canonical_digest(events[0]['result'])
                self.assertTrue(oracle.evaluate(variant,broken)[0])
                broken=copy.deepcopy(capture)
                broken['pi']['summary']['projection']['tool_executions'][0]['is_error']=not broken['pi']['summary']['projection']['tool_executions'][0]['is_error']
                self.assertTrue(oracle.evaluate(variant,broken)[0])
                broken=copy.deepcopy(capture)
                events=[json.loads(line) for line in broken['pi']['stdout_jsonl'].splitlines()]
                events[0]['toolName']='invented-tool'
                broken['pi']['stdout_jsonl']='\n'.join(json.dumps(e) for e in events)
                self.assertTrue(oracle.evaluate(variant,broken)[0])


class PiPatchedHttpTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('HWB_PI_LIVE_TEST') == '1', 'requires installed candidate runtime')
    def test_patched_dispatcher_fetches_compressed_loopback_response(self):
        if os.environ.get('HWB_PI_PROFILE') != 'pi-1.4.0-node-26.13.0':
            self.skipTest('patched HTTP dispatcher belongs to the newer profile')
        import shutil
        package=Path(shutil.which('pi')).resolve().parents[2]
        script=r'''
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {createServer} from 'node:http';
import {gzipSync} from 'node:zlib';
const root=process.argv[1];
const require=createRequire(root+'/package.json');
const version=require('undici/package.json').version;
if(version!=='8.12.3') throw new Error('Unexpected undici '+version);
const {configureHttpDispatcher}=await import(pathToFileURL(root+'/dist/core/http-dispatcher.js'));
configureHttpDispatcher(5000);
const server=createServer((_req,res)=>{
  res.writeHead(200,{'content-type':'application/json','content-encoding':'gzip'});
  res.end(gzipSync(JSON.stringify({ok:true,source:'loopback'})));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
try {
  const response=await fetch('http://127.0.0.1:'+server.address().port,{signal:AbortSignal.timeout(5000)});
  const value=await response.json();
  if(value.ok!==true || value.source!=='loopback') throw new Error('Corrupt response');
  console.log(JSON.stringify({version,...value}));
} finally {
  await new Promise(resolve=>server.close(resolve));
  await require('undici').getGlobalDispatcher().close();
}
'''
        result=subprocess.run(['node','--input-type=module','-e',script,str(package)],
                              env={'PATH':os.environ['PATH'],'NO_PROXY':'127.0.0.1'},
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual({'version':'8.12.3','ok':True,'source':'loopback'},json.loads(result.stdout))
