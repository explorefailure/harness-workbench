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
            execution["result_sha256"]=canonical_digest(event["result"])
        self.assertEqual([],coding_oracle.evaluate(capture)[0])
        for key,value in (("exit_code",0),("wall_time_seconds",True),("truncated",True)):
            broken=copy.deepcopy(capture);raw=copy.deepcopy(LATEST_TOOL_RESULTS)
            raw[2]["result"]["structuredContent"][key]=value
            broken["pi"]["stdout_jsonl"]="\n".join(json.dumps(e) for e in raw)
            broken["pi"]["summary"]["projection"]["tool_executions"][2]["result_sha256"]=canonical_digest(raw[2]["result"])
            self.assertTrue(coding_oracle.evaluate(broken)[0],key)
        broken=copy.deepcopy(capture);broken["workspace"]["after"]=copy.deepcopy(before)
        self.assertTrue(coding_oracle.evaluate(broken)[0])
