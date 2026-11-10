import json
import importlib.util
import py_compile
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
WINE = PROJECT / 'examples/wine'
SCRIPT = HERE / 'import_reference.py'
REPO = '0123456789abcdef0123456789abcdef'


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_dir = tempfile.TemporaryDirectory()
        cls.bin = Path(cls.build_dir.name) / 'smlops'
        subprocess.run(['go', 'build', '-o', str(cls.bin), './cmd/smlops'], cwd=PROJECT, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.root = self.dir / 'wine'
        shutil.copytree(WINE, self.root)
        self.store = self.dir / 'store'
        self.store.mkdir()

    def cli(self, *args):
        return json.loads(subprocess.run([str(self.bin), *args], check=True, capture_output=True, text=True).stdout)

    def run_import(self, *args, success=True):
        cmd = [sys.executable, str(SCRIPT), '--smlops', str(self.bin), '--store', str(self.store), '--repository-id', REPO, '--wine-root', str(self.root), *args]
        p = subprocess.run(cmd, text=True, capture_output=True)
        if success:
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stderr, '')
            self.assertNotIn(str(self.dir), p.stdout)
            return json.loads(p.stdout)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(p.stdout, '')
        self.assertNotIn(str(self.dir), p.stderr)
        self.assertEqual(json.loads(p.stderr)['error']['code'], 'import_failed')
        self.assertFalse((self.store / 'metadata').exists())

    def check_import(self, wheels=None):
        args = ['--wheelhouse', wheels] if wheels else []
        result = self.run_import(*args)
        catalog = (self.store / 'metadata/catalog.json').read_bytes()
        self.assertEqual(self.run_import(*args), result)
        self.assertEqual((self.store / 'metadata/catalog.json').read_bytes(), catalog)
        self.assertEqual(self.cli('repository', 'show', '--store', str(self.store))['sequence'], 3)
        self.assertEqual(result['provenance']['phase'], 'main')
        self.assertEqual(result['provenance']['original_run_id'], '9b54bad3-2fca-47b6-a675-166d2f0042e6')
        snapshot = self.cli('snapshot', 'show', '--store', str(self.store), '--id', result['snapshot']['record_id'])['record']['body']
        run = self.cli('run', 'show', '--store', str(self.store), '--id', result['run']['record_id'])['record']['body']
        data = self.cli('dataset', 'show', '--store', str(self.store), '--id', result['dataset']['record_id'])['record']['body']
        self.assertEqual(snapshot['inputs'][0]['dataset_id'], result['dataset']['record_id'])
        self.assertEqual(snapshot['code']['commit'], '498bdefe3ee7d64db632910e3948c8d147738959')
        self.assertEqual(snapshot['code']['entry_point'], 'wine/pipeline.py')
        self.assertTrue(snapshot['profile_id'].endswith('9057ec64676bcf39558955687af8e6425487a498e6f97de1b72145a966c681df'))
        self.assertEqual(snapshot['parameters']['split.seed'], {'type': 'integer', 'value': 42})
        self.assertEqual(len(snapshot['parameters']), 7)
        self.assertEqual(len(snapshot['environment']['conditions']), 9)
        self.assertEqual(set(snapshot['environment']['conditions']), set(run['observations']))
        self.assertTrue(all('Imported' in o['evidence'] for o in run['observations'].values()))
        self.assertEqual(data['files'][0]['path'], 'wine.data')
        self.assertEqual(run['snapshot_id'], result['snapshot']['record_id'])
        self.assertEqual(run['evidence_source'], 'caller_reported')
        self.assertEqual(run['started_at'], '2026-11-09T13:15:32.383712Z')
        self.assertEqual(run['finished_at'], '2026-11-09T13:15:33.221897Z')
        self.assertEqual(set(run['metrics']), {'model.accuracy', 'model.balanced_accuracy', 'dummy.accuracy', 'dummy.balanced_accuracy'})
        self.assertEqual(len(run['outputs']), 5)
        mats = snapshot['materials']
        self.assertEqual(len(mats), 18)
        self.assertTrue(all(m['required'] for m in mats))
        wheel_mats = [m for m in mats if m['name'].startswith('wheel:')]
        self.assertEqual(len(wheel_mats), 7)
        self.assertTrue(all(m['source']['kind'] == ('object' if wheels else 'external') for m in wheel_mats))
        plan = self.cli('recovery', 'plan', '--store', str(self.store), '--run', result['run']['record_id'])
        self.assertEqual(plan['stored_materials_ready'], bool(wheels))
        self.assertEqual(len(plan['blockers']), 0 if wheels else 7)
        self.assertEqual(plan['execution'], 'unchecked')

    def test_import_external_and_retry(self):
        self.check_import()

    @unittest.skipUnless(os.environ.get('WINE_WHEELHOUSE'), 'WINE_WHEELHOUSE not configured')
    def test_import_locked_wheels_and_retry(self):
        self.check_import(os.environ['WINE_WHEELHOUSE'])

    def test_retry_after_partial_registration(self):
        wrapper = self.dir / 'fail-snapshot'
        marker = self.dir / 'failure-marker'
        wrapper.write_text(
            '#!' + sys.executable + '\n'
            'import json, os, sys\n'
            'from pathlib import Path\n'
            'if sys.argv[1:3] == ["snapshot", "create"] and not Path(' + repr(str(marker)) + ').exists():\n'
            '    Path(' + repr(str(marker)) + ').touch()\n'
            '    print(json.dumps({"error":{"code":"storage"}}), file=sys.stderr)\n'
            '    sys.exit(1)\n'
            'os.execv(' + repr(str(self.bin)) + ', [' + repr(str(self.bin)) + '] + sys.argv[1:])\n'
        )
        wrapper.chmod(0o700)
        cmd = [sys.executable, str(SCRIPT), '--smlops', str(wrapper), '--store', str(self.store), '--repository-id', REPO, '--wine-root', str(self.root)]
        failed = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.cli('repository', 'show', '--store', str(self.store))['sequence'], 1)
        result = self.run_import()
        self.assertEqual(result['dataset']['sequence'], 1)
        self.assertEqual(result['snapshot']['sequence'], 2)
        self.assertEqual(result['run']['sequence'], 3)
        self.assertEqual(self.cli('repository', 'show', '--store', str(self.store))['sequence'], 3)

    def test_bytecode_cache_does_not_replace_verified_source(self):
        source = self.root / 'pipeline.py'
        original = source.read_bytes()
        substitute = b"CACHE_TEST_MARKER = 'benign cached module'\n"
        source.write_bytes(substitute + b'#' * (len(original) - len(substitute)))
        stat = source.stat()
        cache = Path(importlib.util.cache_from_source(str(source)))
        py_compile.compile(str(source), cfile=str(cache), doraise=True)
        source.write_bytes(original)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        # Confirm that the standard timestamp-based loader would use this cache.
        check = subprocess.run([sys.executable, '-c',
            "import importlib.util,sys; s=importlib.util.spec_from_file_location('example',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.CACHE_TEST_MARKER)",
            str(source)], capture_output=True, text=True, check=True)
        self.assertEqual(check.stdout.strip(), 'benign cached module')
        result = self.run_import()
        self.assertEqual(result['run']['sequence'], 3)

    def test_drift_between_preflight_and_object_put(self):
        cases = ['bundle', 'manifest', 'output']
        if os.environ.get('WINE_WHEELHOUSE'):
            cases.append('wheel')
        for case in cases:
            with self.subTest(case=case):
                trial = self.dir / case
                trial.mkdir()
                root = trial / 'wine'
                shutil.copytree(WINE, root)
                store = trial / 'store'
                store.mkdir()
                extra = []
                if case == 'wheel':
                    wheelhouse = trial / 'wheels'
                    shutil.copytree(os.environ['WINE_WHEELHOUSE'], wheelhouse)
                    target = next(wheelhouse.glob('*.whl'))
                    extra = ['--wheelhouse', str(wheelhouse)]
                else:
                    targets = {'bundle': root / 'config.json', 'manifest': root / 'manifest.json',
                               'output': root / 'evidence/main-v1/reference/result/metrics.json'}
                    target = targets[case]
                wrapper = trial / 'drift-cli'
                marker = trial / 'modified'
                wrapper.write_text(
                    '#!' + sys.executable + '\n'
                    'import os, sys\n'
                    'from pathlib import Path\n'
                    'p=Path(' + repr(str(target)) + '); marker=Path(' + repr(str(marker)) + ')\n'
                    'if sys.argv[1:3] == ["object", "put"] and not marker.exists():\n'
                    '    data=p.read_bytes(); p.write_bytes(bytes([data[0] ^ 1]) + data[1:]); marker.touch()\n'
                    'os.execv(' + repr(str(self.bin)) + ', [' + repr(str(self.bin)) + '] + sys.argv[1:])\n'
                )
                wrapper.chmod(0o700)
                cmd = [sys.executable, str(SCRIPT), '--smlops', str(wrapper), '--store', str(store), '--repository-id', REPO, '--wine-root', str(root), *extra]
                result = subprocess.run(cmd, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(result.stdout, '')
                self.assertTrue(marker.exists())
                state = self.cli('repository', 'show', '--store', str(store))
                self.assertEqual(state['sequence'], 0)

    def test_reject_corrupt_input(self):
        (self.root / 'data/wine.data').write_text('corrupt')
        self.run_import(success=False)

    def test_reject_corrupt_output(self):
        (self.root / 'evidence/main-v1/reference/result/predictions.csv').write_text('corrupt')
        self.run_import(success=False)

    def test_reject_incomplete_wheelhouse(self):
        wheels = self.dir / 'wheels'
        wheels.mkdir()
        self.run_import('--wheelhouse', str(wheels), success=False)

    def test_reject_untrusted_code_before_import(self):
        (self.root / 'pipeline.py').write_text("raise RuntimeError('untrusted code executed')")
        self.run_import(success=False)


if __name__ == '__main__':
    unittest.main()
