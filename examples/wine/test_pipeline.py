import copy
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline as p

ROOT = Path(__file__).resolve().parent

class WineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'bundle'
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns('__pycache__'))
        self.rows, self.split, self.config = p.verify(self.root)
        self.source_patch = patch.object(p, '__file__', str(self.root / 'pipeline.py'))
        self.source_patch.start()
    def tearDown(self):
        self.source_patch.stop()
        self.temp.cleanup()
    def remanifest(self): p.write(self.root / 'manifest.json', p.manifest(self.root))
    def test_bad_materials_and_split(self):
        path = self.root / 'data/wine.data'
        path.unlink()
        with self.assertRaises(Exception): p.verify(self.root)
        shutil.copy(ROOT / 'data/wine.data', path)
        path.write_bytes(path.read_bytes() + b'x')
        with self.assertRaises(Exception): p.verify(self.root)
        shutil.copy(ROOT / 'data/wine.data', path)
        for mode in ('duplicate', 'overlap', 'omission'):
            split = copy.deepcopy(self.split)
            if mode == 'duplicate': split['train'][1] = split['train'][0]
            if mode == 'overlap': split['test'][0] = split['train'][0]
            if mode == 'omission': split['test'].pop()
            p.write(self.root / 'split.json', split); self.remanifest()
            with self.assertRaises(ValueError): p.verify(self.root)
    def test_source_binding_and_input_mutation(self):
        other = self.base / 'other'
        shutil.copytree(self.root, other)
        (other / 'pipeline.py').write_text("raise RuntimeError('other code')\n")
        p.write(other / 'manifest.json', p.manifest(other))
        result = subprocess.run([sys.executable, str(ROOT / 'pipeline.py'), 'run', '--root', str(other), '--out', str(self.base / 'mismatch')], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p.read(self.base / 'mismatch/result.json')['status'], 'failed')
        original_train = p.train
        def mutate(*args):
            result = original_train(*args)
            with (self.root / 'config.json').open('a') as f: f.write(' ')
            return result
        with patch.object(p, 'train', mutate):
            with self.assertRaises(ValueError): p.run(self.root, self.base / 'changed')
        self.assertEqual(p.read(self.base / 'changed/result.json')['status'], 'failed')

    def test_protocol_and_phase(self):
        draft = p.read(self.root / 'protocol.json')
        draft.update(status='draft', freeze=None)
        p.write(self.root / 'protocol.json', draft); self.remanifest()
        with self.assertRaises(ValueError): p.run(self.root, self.base / 'main', 'main')
        self.assertEqual(p.read(self.base / 'main/result.json')['status'], 'failed')
        (self.root / 'protocol.json').write_text('not json')
        self.remanifest()
        with self.assertRaises(ValueError): p.verify(self.root)
        shutil.copy(ROOT / 'protocol.json', self.root / 'protocol.json')
        protocol = p.read(self.root / 'protocol.json')
        protocol.update(status='frozen', freeze={'code_revision': 'a' * 40,
            'code_files': {name: p.sha(self.root / name) for name in ('pipeline.py', 'prepare_split.py')},
            'frozen_at': '2026-11-09T12:00:00+00:00'})
        p.write(self.root / 'protocol.json', protocol); self.remanifest()
        self.assertEqual(p.run(self.root, self.base / 'frozen', 'main')['phase'], 'main')
        protocol['freeze']['code_files']['pipeline.py'] = '0' * 64
        p.write(self.root / 'protocol.json', protocol); self.remanifest()
        with self.assertRaises(ValueError): p.verify(self.root)

    def test_control_configuration(self):
        a = self.base / 'a'; p.run(self.root, a)
        other = self.base / 'control'; shutil.copytree(self.root, other)
        config = p.read(other / 'config.json'); config['C'] = 2.0
        p.write(other / 'config.json', config); p.write(other / 'manifest.json', p.manifest(other))
        with patch.object(p, '__file__', str(other / 'pipeline.py')):
            p.run(other, self.base / 'b')
        compared = p.compare(self.root, a, other, self.base / 'b')
        self.assertEqual((compared['materials'], compared['execution'], compared['numerical']), ('different', 'valid', 'incomparable'))

    def test_invalid_evidence_after_rehash(self):
        a, b = self.base / 'a', self.base / 'b'
        p.run(self.root, a); p.run(self.root, b)
        paths = ('predictions.csv', 'metrics.json', 'model.json', 'environment.json', 'result.json')
        original = {directory: {name: (directory/name).read_bytes() for name in paths} for directory in (a, b)}
        def restore():
            for directory in (a, b):
                for name, data in original[directory].items(): (directory/name).write_bytes(data)
        def update(directory, filename, change):
            obj = p.read(directory/filename); change(obj); p.write(directory/filename, obj)
            if filename != 'result.json':
                rec = p.read(directory/'result.json'); rec['outputs'][filename] = p.sha(directory/filename); p.write(directory/'result.json', rec)
        def reject():
            comparison = p.compare(self.root, a, self.root, b)
            self.assertEqual(comparison['execution'], 'invalid')
            self.assertEqual(comparison['numerical'], 'unchecked')
        for invalid in ('python', 'package', 'lock', 'blas'):
            restore()
            for directory in (a, b):
                def modify(env):
                    if invalid == 'python': env['python_version'] = '0.0.0'
                    if invalid == 'package': env['packages']['numpy'] = '0.0'
                    if invalid == 'lock': env['lock_sha256'] = 'invalid'
                    if invalid == 'blas':
                        for backend in env['backends']:
                            if backend['internal_api'] == 'openblas': backend['architecture'] = backend['threading_layer'] = None
                update(directory, 'environment.json', modify)
            reject()
        for invalid in ('id', 'model', 'bool', 'floatcount', 'timestamp'):
            restore()
            if invalid == 'id': update(b, 'result.json', lambda r: r.update(run_id=''))
            if invalid == 'timestamp': update(b, 'result.json', lambda r: r.update(finished_at='tomorrow'))
            if invalid == 'model':
                p.write(b/'model.json', None)
                record = p.read(b/'result.json'); record['outputs']['model.json'] = p.sha(b/'model.json'); p.write(b/'result.json', record)
            if invalid == 'bool': update(b, 'metrics.json', lambda m: m['model']['accuracy'].update(value=True))
            if invalid == 'floatcount': update(b, 'metrics.json', lambda m: m['model'].update(correct=45.0))
            reject()
        restore()
        with (b/'predictions.csv').open(newline='') as f: rows = list(csv.DictReader(f))
        rows[0]['model'] = str((int(rows[0]['model']) % 3) + 1)
        with (b/'predictions.csv').open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['row_id','y_true','model','dummy']); writer.writeheader(); writer.writerows(rows)
        y = [int(row['y_true']) for row in rows]
        m = {k: p.metrics(y, [int(row[k]) for row in rows]) for k in ('model', 'dummy')}
        p.write(b/'metrics.json', m)
        record = p.read(b/'result.json')
        for name in ('metrics.json', 'predictions.csv'): record['outputs'][name] = p.sha(b/name)
        p.write(b/'result.json', record)
        self.assertEqual(p.compare(self.root, a, self.root, b)['numerical'], 'different')
        restore(); update(b, 'result.json', lambda r: r.update(status='failed'))
        result = p.compare(self.root, a, self.root, b)
        self.assertEqual((result['execution'], result['numerical']), ('failed', 'unchecked'))

    def test_fraction_rounding(self):
        y = [1]*15 + [2]*18 + [3]*12
        pred = [2]*15 + [1]*18 + [3]*11 + [1]
        m = p.metrics(y, pred)['balanced_accuracy']
        self.assertEqual((m['numerator'], m['denominator'], m['value']), (11, 36, float(p.Fraction(11, 36))))
    def test_no_test_leakage(self):
        _, _, _, baseline = p.train(self.rows, self.split, self.config)
        altered = copy.deepcopy(self.rows)
        for i in self.split['test']:
            altered[i-1] = (1 if altered[i-1][0] != 1 else 2, [x + 123456 for x in altered[i-1][1]])
        _, _, _, changed = p.train(altered, self.split, self.config)
        self.assertEqual(baseline, changed)
    def test_convergence_warning_fails(self):
        from sklearn.linear_model import LogisticRegression
        from sklearn.exceptions import ConvergenceWarning
        def fail(*args, **kwargs):
            import warnings
            warnings.warn('failure', ConvergenceWarning)
        with patch.object(LogisticRegression, 'fit', fail):
            with self.assertRaises(ConvergenceWarning): p.train(self.rows, self.split, self.config)
    def test_compare_tampering_and_no_clobber(self):
        a, b = self.base / 'a', self.base / 'b'
        p.run(self.root, a); p.run(self.root, b)
        self.assertEqual(p.compare(self.root, a, self.root, b)['numerical'], 'pass')
        self.assertNotEqual(p.compare(self.root, a, self.root, a)['numerical'], 'pass')
        with self.assertRaises(FileExistsError): p.run(self.root, a)
        original = {x: (b/x).read_bytes() for x in ('predictions.csv', 'metrics.json', 'environment.json', 'result.json')}
        def restore():
            for name, data in original.items(): (b/name).write_bytes(data)
        def rehash(name):
            record = p.read(b/'result.json'); record['outputs'][name] = p.sha(b/name); p.write(b/'result.json', record)
        for column in ('y_true', 'model', 'row_id'):
            restore()
            with (b/'predictions.csv').open(newline='') as f: rows = list(csv.DictReader(f))
            rows[0][column] = str((int(rows[0][column]) % 3) + 1)
            with (b/'predictions.csv').open('w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['row_id','y_true','model','dummy']); writer.writeheader(); writer.writerows(rows)
            rehash('predictions.csv')
            self.assertNotEqual(p.compare(self.root, a, self.root, b)['numerical'], 'pass')
        for mode in ('missing', 'count'):
            restore(); m = p.read(b/'metrics.json')
            if mode == 'missing': del m['model']['accuracy']
            else: m['dummy']['correct'] += 1
            p.write(b/'metrics.json', m); rehash('metrics.json')
            self.assertNotEqual(p.compare(self.root, a, self.root, b)['numerical'], 'pass')
        restore(); env = p.read(b/'environment.json'); env['libc'][1] = 'different'; p.write(b/'environment.json', env); rehash('environment.json')
        self.assertEqual(p.compare(self.root, a, self.root, b)['numerical'], 'incomparable')
        restore(); (b/'model.json').unlink()
        self.assertNotEqual(p.compare(self.root, a, self.root, b)['numerical'], 'pass')

if __name__ == '__main__': unittest.main()
