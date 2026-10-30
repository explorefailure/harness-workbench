"""OpenCode builder boundaries and optional real native-tool repair."""
from __future__ import annotations

import copy
import fnmatch
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'src/harness_workbench/opencode_builder'
sys.path.insert(0, str(ROOT / 'src'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


normalizer = load('opencode_normalizer', DATA / 'normalizer.py')
provider = load('opencode_scripted', DATA / 'scripted_provider.py')
with patch.dict(sys.modules, {'normalizer': normalizer, 'scripted_provider': provider}):
    adapter = load('opencode_adapter', DATA / 'adapter.py')


# Reduced from native 1.19.5 JSON. The nonzero shell exit is deliberately a
# completed tool call, so transport success cannot stand in for task success.
EVENTS = [
    {'type': 'step_start', 'sessionID': 's', 'part': {'type': 'step-start', 'sessionID': 's'}},
    {'type': 'tool_use', 'sessionID': 's', 'part': {'type': 'tool', 'sessionID': 's',
        'callID': 'c1', 'tool': 'bash', 'state': {'status': 'completed',
        'input': {'command': 'exit 7'}, 'output': '', 'metadata': {'exit': 7}}}},
    {'type': 'step_finish', 'sessionID': 's', 'part': {'type': 'step-finish', 'sessionID': 's', 'reason': 'tool-calls'}},
    {'type': 'step_start', 'sessionID': 's', 'part': {'type': 'step-start', 'sessionID': 's'}},
    {'type': 'text', 'sessionID': 's', 'part': {'type': 'text', 'sessionID': 's', 'text': 'Done'}},
    {'type': 'step_finish', 'sessionID': 's', 'part': {'type': 'step-finish', 'sessionID': 's', 'reason': 'stop'}},
]


def encoded(events):
    return ('\n'.join(json.dumps(e) for e in events) + '\n').encode()


class OpenCodeBuilderTests(unittest.TestCase):
    def command(self, *args, extra_env=None):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
        env.update(extra_env or {})
        return subprocess.run([sys.executable, '-m', 'harness_workbench._opencode_cli', *map(str, args)],
            env=env, capture_output=True, text=True, timeout=100)

    def test_owned_workspace_and_output_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'owned space'
            self.assertEqual(0, self.command('init', dest).returncode)
            sentinel = dest / 'task.md'
            sentinel.write_text('owner edit')
            self.assertEqual(2, self.command('init', dest).returncode)
            self.assertEqual('owner edit', sentinel.read_text())
            link = Path(temp) / 'dangling'
            link.symlink_to(Path(temp) / 'absent')
            self.assertEqual(2, self.command('init', link).returncode)
            output = Path(temp) / 'evidence.json'
            output.write_text('preserve')
            self.assertEqual(2, self.command('doctor', dest, '--output', output).returncode)
            self.assertEqual('preserve', output.read_text())
            for bad in ('0', '-1', 'nan', 'inf'):
                self.assertEqual(2, self.command('demo', dest, '--timeout', bad).returncode)

    def test_all_owned_assets_ship(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        patterns = project['tool']['setuptools']['package-data']['harness_workbench']
        self.assertEqual('harness_workbench._opencode_cli:main', project['project']['scripts']['hwb-opencode'])
        for file in DATA.rglob('*'):
            if not file.is_file() or '__pycache__' in file.parts:
                continue
            name = file.relative_to(DATA.parent).as_posix()
            self.assertTrue(any(len(name.split('/')) == len(p.split('/')) and
                all(fnmatch.fnmatch(a, b) for a, b in zip(name.split('/'), p.split('/')))
                for p in patterns), name)

    def test_inputs_bind_owned_sources_and_refuse_escaping_or_unlisted_files(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'owned'
            self.command('init', dest)
            path = dest / 'adapter_config.json'
            original = json.loads(path.read_text())
            self.assertEqual('scripted', adapter.load_config(path)['provider']['mode'])
            for change in ('missing', 'escape', 'link', 'unlisted', 'unknown'):
                config = copy.deepcopy(original)
                if change == 'missing':
                    config['inputs'].remove('adapter_config.json')
                elif change == 'escape':
                    config['inputs'].append('../outside')
                elif change == 'link':
                    (dest / 'coding_fixture/link').symlink_to('/etc/hosts')
                elif change == 'unlisted':
                    (dest / 'coding_fixture/unlisted.txt').write_text('x')
                else:
                    config['surprise'] = True
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError, msg=change):
                    adapter.load_config(path)
                (dest / 'coding_fixture/link').unlink(missing_ok=True)
                (dest / 'coding_fixture/unlisted.txt').unlink(missing_ok=True)

    def test_config_never_accepts_embedded_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'owned'; self.command('init', dest)
            path = dest / 'adapter_config.json'; config = json.loads(path.read_text())
            for endpoint in ('file:///tmp/model', 'http://user:password@localhost/v1', 'http://localhost/v1?key=x'):
                config['provider'] = {'mode': 'local', 'base_url': endpoint, 'model': 'qwen'}
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    adapter.load_config(path)

    def test_environment_drops_accounts_and_ambient_configuration(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            'SECRET_NEW_PROVIDER_TOKEN': 'do-not-pass', 'OPENCODE_CONFIG_CONTENT': 'ambient',
            'XDG_DATA_HOME': '/outside', 'HOME': '/outside', 'PYTHONPATH': '/outside'}):
            root = Path(temp)
            env = adapter.environment(root)
            for name in ('SECRET_NEW_PROVIDER_TOKEN', 'OPENCODE_CONFIG_CONTENT', 'PYTHONPATH'):
                self.assertNotIn(name, env)
            self.assertEqual(str(root / 'data'), env['XDG_DATA_HOME'])
            self.assertEqual(str(root / 'home'), env['HOME'])
            self.assertEqual('true', env['OPENCODE_DISABLE_PROJECT_CONFIG'])
            self.assertEqual('true', env['npm_config_offline'])
            self.assertEqual(str(root / 'empty.npmrc'), env['npm_config_userconfig'])

    def test_wrong_binary_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); dest = root / 'owned'; self.command('init', dest)
            marker = root / 'executed'
            fake = root / 'opencode'; fake.write_text(f'#!/bin/sh\ntouch {marker}\n'); fake.chmod(0o755)
            result = self.command('doctor', dest, '--opencode', fake)
            self.assertEqual(2, result.returncode)
            self.assertIn('identity mismatch', json.loads(result.stdout)['error'])
            self.assertFalse(marker.exists())

    def test_normalizer_retains_nonzero_exit_and_rejects_incomplete_or_mixed_streams(self):
        result = normalizer.summarize(encoded(EVENTS))
        self.assertTrue(result['valid'], result)
        self.assertEqual(7, result['tools'][0]['metadata']['exit'])
        corruptions = [EVENTS[:-1], EVENTS[1:], EVENTS + [EVENTS[1]],
            EVENTS[:2] + [EVENTS[1]] + EVENTS[2:]]
        mixed = copy.deepcopy(EVENTS); mixed[1]['sessionID'] = 'other'; corruptions.append(mixed)
        invalid = copy.deepcopy(EVENTS); invalid[1]['part']['state']['status'] = 'running'; corruptions.append(invalid)
        for bad_exit in (True, None, '0'):
            invalid = copy.deepcopy(EVENTS); invalid[1]['part']['state']['metadata']['exit'] = bad_exit; corruptions.append(invalid)
        corruptions.append(EVENTS + EVENTS)
        invalid = copy.deepcopy(EVENTS); invalid[2]['part']['reason'] = 'stop'; corruptions.append(invalid)
        for events in corruptions:
            self.assertFalse(normalizer.summarize(encoded(events))['valid'])
        self.assertFalse(normalizer.summarize(encoded(EVENTS) + b'{broken')['valid'])
        self.assertFalse(normalizer.summarize(b'[]\n')['valid'])

    @unittest.skipUnless(os.environ.get('HWB_OPENCODE_LIVE_TEST') == '1', 'set HWB_OPENCODE_LIVE_TEST=1 with pinned OpenCode')
    def test_real_repair_and_negative_independent_outcome_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'owned space'; self.command('init', dest)
            launcher = os.environ.get('HWB_OPENCODE_BIN', 'opencode')
            result = self.command('doctor', dest, '--opencode', launcher)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            result = self.command('demo', dest, '--opencode', launcher)
            self.assertEqual(0, result.returncode, result.stdout[-6000:] + result.stderr)
            record = json.loads(result.stdout)
            self.assertTrue(record['outcome']['passed'])
            self.assertEqual([1, 0], record['outcome']['native_bash_exits'])
            self.assertEqual(['slugger.py'], record['outcome']['changed_paths'])
            self.assertEqual(record['process']['stdout']['sha256'], __import__('hashlib').sha256(
                (Path(record['workspace']['retained_root']) / 'stdout.jsonl').read_bytes()).hexdigest())
            from harness_workbench.capture import Bounded
            def execution(code):
                return Bounded(['python'], code, None, b'', b'', 0, 0, False, False, False, False)
            good = adapter.evaluate_demo(record, execution(1), execution(0), True)
            self.assertTrue(good['passed'])
            for mutation in ('tests changed', 'lying bash', 'missing edit', 'failed final tests'):
                altered = copy.deepcopy(record)
                if mutation == 'tests changed':
                    altered['workspace']['after'][1]['sha256'] = 'f' * 64
                elif mutation == 'lying bash':
                    altered['summary']['tools'][1]['metadata']['exit'] = 0
                elif mutation == 'missing edit':
                    altered['summary']['tools'][2]['status'] = 'error'
                final = execution(1 if mutation == 'failed final tests' else 0)
                self.assertFalse(adapter.evaluate_demo(altered, execution(1), final, True)['passed'], mutation)
            self.assertFalse((dest / 'runtime').exists())


if __name__ == '__main__':
    unittest.main()
