import copy
import csv
import json
import shutil
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
    def tearDown(self): self.temp.cleanup()
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
        restore(); env = p.read(b/'environment.json'); env['machine'] = 'different'; p.write(b/'environment.json', env); rehash('environment.json')
        self.assertEqual(p.compare(self.root, a, self.root, b)['numerical'], 'incomparable')
        restore(); (b/'model.json').unlink()
        self.assertNotEqual(p.compare(self.root, a, self.root, b)['numerical'], 'pass')

if __name__ == '__main__': unittest.main()
