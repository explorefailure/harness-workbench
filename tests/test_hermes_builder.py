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
auth = load('hermes_provider_auth', DATA / 'provider_auth.py')
checks = load('hermes_task_checks', DATA / 'task_checks.py')
with patch.dict(sys.modules, {'provider_auth': auth, 'task_checks': checks, 'normalizer': normalizer, 'scripted_provider': provider, 'runtime_identity': identity}):
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
            # The runtime path records instead: any checkout may run once identified.
            seen = identity.inspect_source(source, pin, env)
            self.assertEqual(['source_commit', 'release_tag', 'tag_object', 'source_tree_sha256'], seen['pin_mismatches'])
            self.assertIsNone(seen['release_tag'])
            (source / 'untracked').write_text('would run undigested')
            with self.assertRaisesRegex(ValueError, 'dirty'):identity.inspect_source(source, pin, env)

    def fixture_runtime(self, root, hermes_answer='Hermes Agent v9.9.9 (fixture)'):
        source = root / 'source'; (source / '.venv/bin').mkdir(parents=True)
        def git(*args):
            return subprocess.check_output(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args], text=True).strip()
        git('init', '-q')
        (source / '.gitignore').write_text('.venv/\n')
        (source / 'uv.lock').write_text('graph')
        (source / 'hermes').write_text('launcher')
        git('add', '.'); git('commit', '-qm', 'Fixture'); git('tag', '-a', 'v9.9.9', '-m', 'fixture')
        python = source / '.venv/bin/python'
        python.write_text('#!/bin/sh\ncase "$*" in\n'
            '*sys.version_info*) echo 3.12.1;;\n'
            '*print_fast_version_info*) echo "%s";;\n'
            '*) exit 1;;\nesac\n' % hermes_answer)
        uv = root / 'uv'
        uv.write_text('#!/bin/sh\n[ "$1" = --version ] && echo "uv 0.1.0" && exit 0\nexit 1\n')
        for path in (python, uv): path.chmod(0o755)
        return source, uv

    def test_runtime_differences_are_recorded_not_refused(self):
        pin = json.loads((DATA / 'pin.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source, uv = self.fixture_runtime(root)
            python, seen = identity.verify_runtime(source, pin, root / 'measurement', str(uv))
            self.assertEqual(source / '.venv/bin/python', python)
            self.assertEqual('9.9.9', seen['version'])
            self.assertEqual('v9.9.9', seen['release_tag'])
            self.assertEqual(['source_commit', 'release_tag', 'tag_object', 'uv_lock_sha256', 'source_tree_sha256',
                              'uv_version', 'python_version', 'dependency_check', 'anthropic_version', 'version'],
                             seen['differs_from_pin'])
            self.assertEqual(pin['version'], seen['pinned']['version'])
            self.assertFalse(seen['dependency_check']['passed'])
            self.assertIsNone(seen['anthropic_sdk_version'])

    def test_runtime_that_does_not_answer_like_hermes_is_refused(self):
        pin = json.loads((DATA / 'pin.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source, uv = self.fixture_runtime(root, hermes_answer='something else 1.0')
            with self.assertRaisesRegex(ValueError, 'does not answer like Hermes'):
                identity.verify_runtime(source, pin, root / 'measurement', str(uv))

    def test_cli_falls_back_to_store_hermes_and_its_uv(self):
        from harness_workbench import _hermes_cli
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / 'store'; version = store / 'hermes' / '0.22.5'
            source = version / 'source'; (source / '.git').mkdir(parents=True)
            uv_bin = store / 'uv' / '0.12.21' / 'bin'; uv_bin.mkdir(parents=True); (uv_bin / 'uv').write_text('')
            from harness_workbench import runtimes
            (version / 'install.json').write_text(json.dumps({'schema': runtimes.RECORD_SCHEMA, 'kind': 'uv-source',
                'source_root': str(source), 'uv': {'version': '0.12.21', 'source': 'store', 'bin': str(uv_bin)}}))
            with patch.dict(os.environ, {runtimes.STORE_ENV: str(store)}):
                self.assertEqual((str(source), str(uv_bin / 'uv')), _hermes_cli.store_runtime())
                (uv_bin / 'uv').unlink()
                self.assertEqual((str(source), None), _hermes_cli.store_runtime())
            with patch.dict(os.environ, {runtimes.STORE_ENV: str(Path(temp) / 'empty')}):
                self.assertEqual((None, None), _hermes_cli.store_runtime())
            dest = Path(temp) / 'builder'; self.assertEqual(0, self.command('init', dest).returncode)
            seen = []
            with patch.dict(os.environ, {runtimes.STORE_ENV: str(store)}), \
                 patch.object(_hermes_cli.os, 'execv', lambda exe, argv: seen.append(argv)), \
                 patch.object(_hermes_cli.os, 'chdir', lambda path: None):
                _hermes_cli.main(['doctor', str(dest)])
                (dest / 'runtime' / 'source').mkdir(parents=True)
                _hermes_cli.main(['doctor', str(dest)])
            self.assertIn('--runtime', seen[0]); self.assertEqual(str(source.resolve()), seen[0][seen[0].index('--runtime') + 1])
            self.assertEqual('uv', seen[0][seen[0].index('--uv') + 1])  # store uv gone: PATH uv
            self.assertNotIn('--runtime', seen[1])  # the workspace's own runtime wins


    @unittest.skipUnless(os.environ.get('HWB_HERMES_BASE_RUNTIME'), 'requires retained core-only Hermes runtime')
    def test_core_only_runtime_differences_are_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'builder';self.command('init',dest)
            result=self.command('doctor',dest,'--runtime',os.environ['HWB_HERMES_BASE_RUNTIME'])
            # Missing extras are recorded, not refused; a run cannot pass on a check-only doctor.
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)
            self.assertIn('dependency_check',json.loads(result.stdout)['runtime']['differs_from_pin'])
            self.assertFalse(list(dest.glob('.hwb-hermes-*/hermes-home/gateway.pid')))

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
            doctor_record=json.loads(result.stdout)
            self.assertEqual(['anthropic'],doctor_record['runtime']['optional_extras'])
            self.assertEqual('0.87.0',doctor_record['runtime']['anthropic_sdk_version'])
            result = self.command('demo',dest,'--runtime',runtime)
            self.assertEqual(0,result.returncode,result.stdout[-5000:]+result.stderr)
            record=json.loads(result.stdout)
            self.assertTrue(record['gateway_cleanup']['result']['passed'])
            launch=json.loads((Path(record['workspace']['retained_root'])/'launch.json').read_text())
            query=launch['argv'][launch['argv'].index('--query')+1]
            self.assertIn('Your task workspace is '+record['workspace']['retained_root']+'/fixture',query)
            self.assertIn('do not search or modify sibling or ancestor directories',query)
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
