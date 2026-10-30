"""Owned Hermes workflow, native protocol and independent repair boundaries."""
import copy
import fnmatch
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'src/harness_workbench/hermes_builder'
sys.path.insert(0, str(ROOT / 'src'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


normalizer = load('hermes_normalizer', DATA / 'normalizer.py')
provider = load('hermes_scripted', DATA / 'scripted_provider.py')
identity = load('hermes_identity', DATA / 'runtime_identity.py')
cleanup = load('hermes_cleanup', DATA / 'gateway_cleanup.py')
with patch.dict(sys.modules, {'normalizer': normalizer, 'scripted_provider': provider, 'runtime_identity': identity}):
    adapter = load('hermes_adapter', DATA / 'adapter.py')


# Reduced from actual Hermes 0.22.5 events. The shell failure is an intermediate
# observation; a successful terminal result must not erase it.
EVENTS = [
    {'type': 'system', 'subtype': 'init', 'model': 'repair', 'session_id': ''},
    {'type': 'tool_use', 'name': 'terminal', 'tool_call_id': 'c1', 'input': {'command': 'exit 7'}},
    {'type': 'tool_result', 'name': 'terminal', 'tool_call_id': 'c1', 'output': '{"exit_code":7,"output":""}', 'is_error': True},
    {'type': 'text', 'text': 'Done'},
    {'type': 'result', 'session_id': 'local-session', 'exit_code': 0, 'text': 'Done'},
]


def encode(events):
    return ('\n'.join(json.dumps(e) for e in events) + '\n').encode()


class HermesBuilderTests(unittest.TestCase):
    def command(self, *args):
        return subprocess.run([sys.executable, '-m', 'harness_workbench._hermes_cli', *map(str, args)],
            env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')), capture_output=True, text=True, timeout=180)

    def test_owned_workspace_and_existing_evidence_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'owned space'
            result = self.command('init', dest)
            self.assertEqual(0, result.returncode, result.stderr)
            (dest / 'task.md').write_text('owner edit')
            self.assertEqual(2, self.command('init', dest).returncode)
            self.assertEqual('owner edit', (dest / 'task.md').read_text())
            link = Path(temp) / 'dangling'; link.symlink_to(Path(temp) / 'absent')
            self.assertEqual(2, self.command('init', link).returncode)
            output = Path(temp) / 'evidence.json'; output.write_text('preserve')
            self.assertEqual(2, self.command('doctor', dest, '--output', output).returncode)
            self.assertEqual('preserve', output.read_text())
            for bad in ('0', '-1', 'nan', 'inf'):
                self.assertEqual(2, self.command('demo', dest, '--timeout', bad).returncode)

    def test_all_builder_assets_and_entrypoint_ship(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        self.assertEqual('harness_workbench._hermes_cli:main', project['project']['scripts']['hwb-hermes'])
        patterns = project['tool']['setuptools']['package-data']['harness_workbench']
        for file in DATA.rglob('*'):
            if not file.is_file() or '__pycache__' in file.parts:
                continue
            name = file.relative_to(DATA.parent).as_posix()
            self.assertTrue(any(len(name.split('/')) == len(p.split('/')) and
                all(fnmatch.fnmatch(a, b) for a, b in zip(name.split('/'), p.split('/'))) for p in patterns), name)

    def test_config_binds_sources_fixture_and_its_own_name(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'builder'; self.command('init', dest)
            path = dest / 'adapter_config.json'; original = json.loads(path.read_text())
            self.assertEqual('scripted', adapter.load_config(path)['provider']['mode'])
            for mode in ('self', 'identity', 'extra', 'escape', 'symlink'):
                config = copy.deepcopy(original)
                if mode == 'self':config['inputs'].remove('adapter_config.json')
                elif mode == 'identity':config['inputs'].remove('runtime_identity.py')
                elif mode == 'extra':(dest / 'coding_fixture/extra.txt').write_text('undeclared')
                elif mode == 'escape':config['inputs'].append('../outside')
                else:(dest / 'coding_fixture/link').symlink_to('/etc/hosts')
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError, msg=mode):adapter.load_config(path)
                (dest / 'coding_fixture/extra.txt').unlink(missing_ok=True)
                (dest / 'coding_fixture/link').unlink(missing_ok=True)

    def test_local_endpoint_rejects_embedded_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'builder'; self.command('init', dest)
            path = dest / 'adapter_config.json'; config = json.loads(path.read_text())
            for url in ('file:///tmp/model', 'http://user:password@localhost/v1', 'http://localhost/v1?key=x'):
                config['provider'] = {'mode': 'local', 'base_url': url, 'model': 'local'}
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):adapter.load_config(path)

    def test_environment_drops_accounts_and_unrelated_configuration(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'API_NEW_PROVIDER_KEY': 'do-not-pass', 'HERMES_HOME': '/outside', 'PYTHONPATH': '/outside', 'TERMINAL_ENV': 'ssh'}):
            root = Path(temp).resolve(); env = identity.environment(root)
            self.assertNotIn('API_NEW_PROVIDER_KEY', env)
            self.assertNotIn('PYTHONPATH', env)
            self.assertEqual(str(root / 'hermes-home'), env['HERMES_HOME'])
            self.assertEqual('local', env['TERMINAL_ENV'])
            self.assertEqual('1', env['UV_OFFLINE'])
            self.assertEqual('1', env['HERMES_DISABLE_LAZY_INSTALLS'])

    def test_gateway_cleanup_refuses_foreign_or_recycled_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            status = Mock()
            status._pid_exists.return_value = True
            status.get_process_start_time.return_value = 200
            status.start_time_fingerprints_match.return_value = False
            planned = Mock()
            for owner in ('/another/profile', str(home)):
                (home / 'gateway.pid').write_text(json.dumps({'kind':'hermes-gateway',
                    'pid':999999, 'hermes_home':owner, 'start_time':100}))
                with self.assertRaises(ValueError):cleanup.cleanup(home, status, planned)
            status.terminate_pid.assert_not_called()
            planned.assert_not_called()

    def test_native_pairs_and_terminal_are_not_guessed(self):
        result = normalizer.summarize(encode(EVENTS))
        self.assertTrue(result['valid'], result)
        self.assertEqual(7, result['tools'][0]['exit_code'])
        self.assertTrue(result['tools'][0]['is_error'])
        malformed = [EVENTS[:-1], EVENTS[1:], EVENTS + [EVENTS[-1]], EVENTS[:2] + EVENTS[3:], EVENTS[:2] + [EVENTS[1]] + EVENTS[2:]]
        for field, value in [('tool_call_id', 'other'), ('name', 'patch'), ('is_error', 'false'), ('output', '{"exit_code":true}')]:
            changed = copy.deepcopy(EVENTS); changed[2][field] = value; malformed.append(changed)
        failed = copy.deepcopy(EVENTS); failed[-1]['exit_code'] = 1; malformed.append(failed)
        for events in malformed:self.assertFalse(normalizer.summarize(encode(events))['valid'], events)
        self.assertFalse(normalizer.summarize(encode(EVENTS) + b'{broken')['valid'])
        truncated = copy.deepcopy(EVENTS); truncated[2]['output'] = '{"output":"' + 'x' * 5000 + '...'
        summary = normalizer.summarize(encode(truncated))
        self.assertTrue(summary['valid'])
        self.assertIsNone(summary['tools'][0]['exit_code'])
        self.assertFalse(summary['tools'][0]['output_json_decoded'])

    def test_source_pin_binds_working_bytes_before_runtime_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'source'; source.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args], text=True).strip()
            git('init', '-q')
            (source / 'uv.lock').write_text('fixed graph')
            (source / 'hermes').write_text('not executed')
            git('add', '.');git('commit', '-qm', 'Fixture');git('tag', '-a', 'vfixture', '-m', 'fixture')
            env = identity.environment(root / 'measurement')
            pin = {'source_commit':git('rev-parse','HEAD'), 'release_tag':'vfixture', 'tag_object':git('rev-parse','vfixture^{tag}'),
                'uv_lock_sha256':identity.digest_file(source / 'uv.lock'), 'source_tree_sha256':identity.source_digest(source,env)[1], 'version':'fixture'}
            self.assertEqual(pin['source_commit'], identity.verify_source(source,pin,env)['source_commit'])
            (source / 'hermes').write_text('changed launcher')
            with self.assertRaisesRegex(ValueError,'dirty'):identity.verify_source(source,pin,env)
            git('add','.');git('commit','-qm','changed')
            with self.assertRaisesRegex(ValueError,'commit mismatch'):identity.verify_source(source,pin,env)


    @unittest.skipUnless(os.environ.get('HWB_HERMES_LIVE_TEST') == '1', 'requires pinned Hermes runtime')
    def test_timeout_stops_its_owned_gateway(self):
        import time
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'builder'; self.command('init', dest)
            path = dest / 'adapter_config.json'
            config = json.loads(path.read_text())
            with provider.ScriptedProvider(dest / 'coding_fixture') as server:
                # Hold the first model response until after the CLI deadline.
                # The gateway has already started before sending this request.
                def delayed(*args):
                    time.sleep(90)
                    return {'role': 'assistant', 'content': 'late'}, 'stop'
                server.respond = delayed
                config['provider'] = {'mode':'local', 'base_url':server.base_url, 'model':'repair'}
                path.write_text(json.dumps(config))
                result = self.command('run', path, '--runtime', os.environ['HWB_HERMES_RUNTIME'], '--timeout', '45')
            self.assertEqual(1, result.returncode, result.stdout[-3000:] + result.stderr)
            record = json.loads(result.stdout)
            self.assertTrue(server.requests, 'deadline must occur after gateway/model startup: ' + result.stdout[-12000:])
            self.assertEqual('timeout', record['process']['termination_reason'])
            stopped = record['gateway_cleanup']['result']
            self.assertTrue(stopped['passed'], stopped)
            self.assertEqual('stopped', stopped['state'])
            self.assertFalse(stopped['alive_after_cleanup'])

    @unittest.skipUnless(os.environ.get('HWB_HERMES_LIVE_TEST') == '1', 'set HWB_HERMES_LIVE_TEST=1 with pinned Hermes runtime')
    def test_real_owned_repair_and_independent_negative_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'owned space'; self.command('init', dest)
            runtime = os.environ['HWB_HERMES_RUNTIME']
            result = self.command('doctor', dest, '--runtime', runtime)
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)
            result = self.command('demo',dest,'--runtime',runtime)
            self.assertEqual(0,result.returncode,result.stdout[-5000:]+result.stderr)
            record=json.loads(result.stdout)
            self.assertTrue(record['gateway_cleanup']['result']['passed'])
            self.assertFalse(record['gateway_cleanup']['result'].get('alive_after_cleanup', False))
            self.assertTrue(record['outcome']['passed'])
            self.assertEqual([1,0],record['outcome']['native_terminal_exits'])
            self.assertEqual(['slugger.py'],record['outcome']['changed_paths'])
            self.assertEqual(record['inputs'],record['inputs_after'])
            self.assertTrue(record['scripted_provider']['probes'])
            self.assertFalse(record['scripted_provider']['errors'])
            from harness_workbench.capture import Bounded
            def execution(code):return Bounded(['python'],code,None,b'',b'',0,0,False,False,False,False)
            for mutation in ('changed test','false success','missing patch','failed final tests'):
                changed=copy.deepcopy(record)
                if mutation=='changed test':changed['workspace']['after'][1]['sha256']='f'*64
                elif mutation=='false success':changed['summary']['tools'][1]['exit_code']=0
                elif mutation=='missing patch':changed['summary']['tools'][2]['is_error']=True
                final=execution(1 if mutation=='failed final tests' else 0)
                self.assertFalse(adapter.evaluate_demo(changed,execution(1),final,True)['passed'],mutation)


if __name__ == '__main__':unittest.main()
