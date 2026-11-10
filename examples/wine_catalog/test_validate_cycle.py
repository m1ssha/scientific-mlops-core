import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import validate_cycle as cycle

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
WINE = PROJECT / 'examples/wine'
REFERENCE = WINE / 'evidence/main-v1/reference/result'
EVIDENCE = HERE / 'evidence/issue20'


class OracleTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'wine'
        (self.root / 'data').mkdir(parents=True)
        shutil.copyfile(WINE / 'data/wine.data', self.root / 'data/wine.data')
        shutil.copyfile(WINE / 'split.json', self.root / 'split.json')
        self.output = Path(self.tmp.name) / 'output'
        shutil.copytree(REFERENCE, self.output)

    def test_reference_oracle(self):
        result = cycle.oracle(self.root, REFERENCE, self.output)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['scores']['model']['correct'], 45)
        self.assertEqual(result['scores']['dummy']['correct'], 18)

    def test_reject_wrong_json_number_types(self):
        metrics_path = self.output / 'metrics.json'
        original = json.loads(metrics_path.read_text())
        for field, value in [('numerator', True), ('correct', 45.0)]:
            with self.subTest(field=field):
                data = json.loads(json.dumps(original))
                if field == 'numerator': data['model']['accuracy'][field] = value
                else: data['model'][field] = value
                metrics_path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'recomputed metrics'):
                    cycle.oracle(self.root, REFERENCE, self.output)

    def test_reject_wrong_split_types_and_id(self):
        path = self.root / 'split.json'
        initial = json.loads(path.read_text())
        for value in (True, 1.0, 179):
            with self.subTest(value=value):
                data = json.loads(json.dumps(initial)); data['test'][0] = value
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'invalid test split'):
                    cycle.oracle(self.root, REFERENCE, self.output)

    def test_reject_nonfinite_metrics(self):
        path = self.output / 'metrics.json'
        data = json.loads(path.read_text()); data['model']['accuracy']['value'] = float('nan')
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'non-finite'):
            cycle.oracle(self.root, REFERENCE, self.output)

    def test_reject_prediction_drift(self):
        path = self.output / 'predictions.csv'
        with path.open(newline='') as f:
            rows = list(csv.DictReader(f))
        rows[0]['model'] = '2' if rows[0]['model'] != '2' else '1'
        with path.open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['row_id', 'y_true', 'model', 'dummy']); writer.writeheader(); writer.writerows(rows)
        with self.assertRaises(ValueError):
            cycle.oracle(self.root, REFERENCE, self.output)

    def test_public_evidence_hashes_and_oracle(self):
        hashes = json.loads((EVIDENCE / 'sha256.json').read_text())
        files = {str(p.relative_to(EVIDENCE)) for p in EVIDENCE.rglob('*') if p.is_file() and p.name != 'sha256.json'}
        self.assertEqual(files, set(hashes))
        for name, expected in hashes.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), expected, name)
        result = cycle.oracle(WINE, REFERENCE, EVIDENCE / 'repeat-output')
        self.assertEqual(result, json.loads((EVIDENCE / 'oracle.json').read_text()))
        manifest = json.loads((EVIDENCE / 'control-manifest.json').read_text())
        self.assertEqual(manifest['files']['config.json'], hashlib.sha256((EVIDENCE / 'control-config.json').read_bytes()).hexdigest())
        self.assertEqual(json.loads((EVIDENCE / 'control-config.json').read_text())['C'], 2.0)


    def test_public_evidence_links_and_outcomes(self):
        load = lambda name: json.loads((EVIDENCE / name).read_text())
        old = load('original-records.json'); control = load('control-record.json')
        latest = load('latest-ref.json'); plan = load('source-plan.json')
        report = load('repeat-report.json'); repeated = load('repeat-record.json')
        failed = load('failure-report.json'); failed_record = load('failure-record.json')
        self.assertEqual(latest['target_id'], control['id'])
        self.assertEqual(control['body']['predecessor_id'], old['snapshot']['id'])
        self.assertEqual(control['body']['parameters']['config.C']['value'], 2)
        self.assertEqual(plan['snapshot_id'], old['snapshot']['id'])
        self.assertEqual(plan['conditions']['inputs'][0]['dataset_id'], old['dataset']['id'])
        self.assertEqual(plan['conditions']['parameters']['config.C']['value'], 1)
        self.assertEqual((report['materials'], report['runtime'], report['execution'], report['registration'], report['numerical']), ('verified', 'passed', 'succeeded', 'confirmed', 'pass'))
        self.assertEqual(repeated['body']['snapshot_id'], old['snapshot']['id'])
        self.assertEqual(repeated['body']['source_run_id'], old['run']['id'])
        self.assertNotEqual(repeated['id'], old['run']['id'])
        self.assertEqual((failed['materials'], failed['execution'], failed['registration'], failed['outcome']), ('failed', 'unchecked', 'confirmed', 'failed'))
        self.assertEqual(failed_record['body']['source_run_id'], old['run']['id'])
        self.assertEqual(failed_record['body']['snapshot_id'], old['snapshot']['id'])
        self.assertEqual(failed_record['body']['outcome'], 'failed')
        for entry in repeated['body']['outputs']:
            output = EVIDENCE / 'repeat-output' / Path(entry['path']).name
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), entry['object']['digest'])
            self.assertEqual(output.stat().st_size, entry['object']['size'])
        for entry in old['run']['body']['outputs']:
            output = REFERENCE / Path(entry['path']).name
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), entry['object']['digest'])
        self.assertTrue(load('restored-plan.json')['stored_materials_ready'])


@unittest.skipUnless(os.environ.get('WINE_FULL_CYCLE') == '1', 'set WINE_FULL_CYCLE=1 for the historical full cycle')
class FullCycleTests(unittest.TestCase):
    def test_full_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); binary = base / 'smlops'
            subprocess.run(['go', 'build', '-o', str(binary), './cmd/smlops'], cwd=PROJECT, check=True)
            result = cycle.cycle(binary, os.environ['WINE_PYTHON'], os.environ['WINE_WHEELHOUSE'], base / 'scratch', base / 'evidence')
            self.assertEqual(result['status'], 'pass')
            report = json.loads((base / 'evidence/failure-report.json').read_text())
            self.assertEqual((report['execution'], report['outcome'], report['registration']), ('unchecked', 'failed', 'confirmed'))


if __name__ == '__main__':
    unittest.main()
