"""Adapter boundary regressions; native runtimes are replaced only at launch."""
import base64
import copy
import json
from pathlib import Path
import shutil
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_hermes_builder as hermes
import test_opencode_builder as opencode
from harness_workbench.capture import run_bounded, TRUNCATED_CREDENTIAL_CAPTURE


class AdapterRegressions(unittest.TestCase):
    def owned(self, source, parent):
        root = parent / 'owned'
        shutil.copytree(source.DATA, root, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        return root, root / 'adapter_config.json'

    def launch(self, source, root, path, parent, demo=False):
        module = source.adapter
        config = module.load_config(path)
        identity = {'source_root': str(root)}
        environments = []

        def bounded(argv, **kwargs):
            if 'gateway_cleanup.py' in str(argv[1:2]):
                argv = [sys.executable, '-B', '-c', 'print(\'{"passed":true}\')']
            elif argv[0] == '/mock/native' or (len(argv) > 1 and argv[1] == str(root / 'hermes')):
                argv = [sys.executable, '-B', '-c', 'print("native fixture")']
            else:
                environments.append(dict(kwargs['env']))
            return run_bounded(argv, **kwargs)

        binary = Path(sys.executable) if source is hermes else Path('/mock/native')
        with patch.object(module, 'doctor', return_value=(config, binary, identity)), \
                patch.object(module, 'summarize', return_value={'errors': [], 'tools': []}), \
                patch.object(module, 'run_bounded', side_effect=bounded):
            result = module.capture(path, None, 3, str(parent), demo=demo)
        return result, environments

    def test_legacy_configs_migrate_without_rewriting_and_hash_helpers(self):
        for source in (opencode, hermes):
            for mode in ('scripted', 'local'):
                with self.subTest(adapter=source.__name__, mode=mode), tempfile.TemporaryDirectory() as temp:
                    root, path = self.owned(source, Path(temp))
                    config = json.loads(path.read_text())
                    config['inputs'] = [n for n in config['inputs'] if n not in ('provider_auth.py', 'task_checks.py')]
                    config.pop('checks', None)
                    config['provider'] = {'mode': 'scripted'} if mode == 'scripted' else {
                        'mode': 'local', 'base_url': 'http://localhost/v1', 'model': 'fixture'}
                    path.write_text(json.dumps(config))
                    before = path.read_bytes()
                    loaded = source.adapter.load_config(path)
                    self.assertEqual(['provider_auth.py', 'task_checks.py'], loaded['input_migration'])
                    self.assertEqual(before, path.read_bytes())
                    if mode == 'local':
                        result, _ = self.launch(source, root, path, Path(temp))
                        self.assertTrue(result['verdict']['passed'], result['verdict'])
                        self.assertTrue({'provider_auth.py', 'task_checks.py'} <= {n['path'] for n in result['inputs']})

    def test_partial_and_modern_helper_omissions_are_rejected(self):
        for source in (opencode, hermes):
            with tempfile.TemporaryDirectory() as temp:
                root, path = self.owned(source, Path(temp))
                original = json.loads(path.read_text())
                for missing in ({'provider_auth.py'}, {'task_checks.py'}, {'provider_auth.py', 'task_checks.py'}):
                    for modern in (False, True):
                        if not modern and len(missing) == 2:
                            continue  # the explicitly supported legacy migration
                        config = copy.deepcopy(original)
                        if modern:
                            config['provider'] = {'mode': 'compatible', 'base_url': 'http://localhost/v1', 'model': 'fixture'}
                        config['inputs'] = [n for n in config['inputs'] if n not in missing]
                        path.write_text(json.dumps(config))
                        with self.subTest(adapter=source.__name__, missing=missing, modern=modern):
                            with self.assertRaisesRegex(ValueError, 'inputs must declare'):
                                source.adapter.load_config(path)

    def test_checks_cannot_change_delete_or_replace_original_inputs_and_pass(self):
        for source in (opencode, hermes):
            for action in ('write_text("changed")', 'unlink()', 'symlink_to("/etc/hosts")'):
                with self.subTest(adapter=source.__name__, action=action), tempfile.TemporaryDirectory() as temp:
                    root, path = self.owned(source, Path(temp))
                    config = json.loads(path.read_text())
                    config['provider'] = {'mode': 'local', 'base_url': 'http://localhost/v1', 'model': 'fixture'}
                    target = root / config['task']
                    statement = f'p.{action}' if not action.startswith('symlink') else f'p.unlink(); p.{action}'
                    config['checks'] = [{'id': 'mutating-check', 'argv': [sys.executable, '-B', '-c',
                        f'from pathlib import Path; p=Path({str(target)!r}); {statement}']}]
                    path.write_text(json.dumps(config))
                    result, _ = self.launch(source, root, path, Path(temp))
                    self.assertTrue(result['task_checks']['passed'])
                    self.assertFalse(result['verdict']['passed'])
                    self.assertIn('declared source inputs changed during capture', result['verdict']['errors'])
                    self.assertNotEqual(result['inputs'], result['inputs_after'])
                    stored = json.loads((Path(result['workspace']['retained_root']) / 'result.json').read_text())
                    self.assertFalse(stored['verdict']['passed'])

    def test_demo_tests_have_no_selected_credentials_and_outputs_are_redacted(self):
        secret = 'synthetic-review-secret-012345'
        for source in (opencode, hermes):
            with self.subTest(adapter=source.__name__), tempfile.TemporaryDirectory() as temp:
                root, path = self.owned(source, Path(temp))
                config = json.loads(path.read_text())
                config['provider'] = {'mode': 'compatible', 'base_url': 'http://localhost/v1',
                    'model': 'fixture', 'api_key_env': 'REVIEW_SELECTED_KEY'}
                # A model-written test may echo a key already observed in native tools.
                # This synthetic value is deliberately written into a temporary hostile
                # fixture so the test can prove it never survives in retained evidence.
                # codeql[py/clear-text-storage-sensitive-data]
                (root / config['fixture'] / 'test_slugger.py').write_text(f'print({secret!r})\n')
                path.write_text(json.dumps(config))
                with patch.dict('os.environ', {'REVIEW_SELECTED_KEY': secret}):
                    result, envs = self.launch(source, root, path, Path(temp), demo=True)
                self.assertEqual(2, len(envs))
                for env in envs:
                    self.assertNotIn(secret, env.values())
                    self.assertNotIn('REVIEW_SELECTED_KEY', env)
                self.assertNotIn(secret, json.dumps(result))
                for field in ('independent_before', 'independent_after'):
                    captured = result['outcome'][field]['stdout']
                    self.assertNotIn(secret.encode(), base64.b64decode(captured['base64']))
                    self.assertIn('[REDACTED]', captured['text'])

    def test_demo_redaction_fails_closed_for_truncated_independent_output(self):
        secret = secrets.token_hex(16)
        process = run_bounded([sys.executable, '-B', '-c', 'import os; print(os.environ["SYNTHETIC_KEY"] * 100)'],
            cwd=Path('/tmp'), env={'SYNTHETIC_KEY': secret}, timeout=3, stdout_limit=100)
        for source in (opencode, hermes):
            record = {'verdict': {'errors': []}, 'workspace': {'before': [], 'after': []}, 'summary': {'tools': []}}
            outcome = source.adapter.evaluate_demo(record, process, process, False, [secret])
            self.assertNotIn(secret, json.dumps(outcome))
            self.assertEqual(TRUNCATED_CREDENTIAL_CAPTURE, base64.b64decode(outcome['independent_after']['stdout']['base64']))

    def test_pi_final_input_integrity_includes_check_effects(self):
        data = opencode.ROOT / 'src/harness_workbench/pi_builder'
        normalizer = opencode.load('regression_pi_normalizer', data / 'normalizer.py')
        checks = opencode.load('regression_pi_checks', data / 'task_checks.py')
        with patch.dict(sys.modules, {'normalizer': normalizer, 'task_checks': checks}):
            module = opencode.load('regression_pi_adapter', data / 'adapter.py')
        for action in ('pass', 'p.write_text("changed")', 'p.unlink()', 'p.unlink(); p.symlink_to("/etc/hosts")'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                root = parent / 'owned'
                shutil.copytree(data, root, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                path = root / 'coding_adapter_config.json'
                config = json.loads(path.read_text())
                target = root / config['task']
                config['checks'] = [{'id': 'input-integrity', 'argv': [sys.executable, '-B', '-c',
                    f'from pathlib import Path; p=Path({str(target)!r}); {action}']}]
                config['inputs'].append('task_checks.py')
                path.write_text(json.dumps(config))
                runtime = {'pi_path': sys.executable, 'pi_version': '1.4.0', 'node_version': 'mock', 'install_identity': {}}
                native = run_bounded([sys.executable, '-B', '-c', 'print("{}")'], cwd=parent, env={}, timeout=3)
                with patch.dict(sys.modules, {'task_checks': checks}), \
                        patch.object(module, 'check_runtime', return_value=runtime), \
                        patch.object(module, 'run_bounded', return_value=native), \
                        patch.object(module, 'normalize_jsonl', return_value={}), \
                        patch.object(module, 'generic_errors', return_value=[]):
                    result = module.capture(path, workspace_parent=str(parent))
                self.assertTrue(result['task_checks']['passed'])
                self.assertEqual(action == 'pass', result['verdict']['passed'])
                before = result['configuration']['input_digests']
                after = result['configuration']['input_digests_after']
                self.assertEqual(action == 'pass', before == after)
