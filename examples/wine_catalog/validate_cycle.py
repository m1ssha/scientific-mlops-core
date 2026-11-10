#!/usr/bin/env python3
"""Validate a Wine catalog cycle in disposable storage on an eligible host."""
import argparse
import copy
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
NAMES = ('result.json', 'predictions.csv', 'metrics.json', 'model.json', 'environment.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON')))


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')


def run(argv, cwd, expect=0):
    p = subprocess.run([str(x) for x in argv], cwd=cwd, text=True, capture_output=True)
    if (p.returncode == 0) != (expect == 0):
        raise ValueError('unexpected process exit; inspect private scratch')
    if expect == 0:
        return json.loads(p.stdout)
    return p


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_equal(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b))
    return a == b


def oracle(root, original, repeated):
    """Recompute both classifiers independently, using raw input labels and split."""
    with (root / 'data/wine.data').open(newline='') as f:
        labels = [int(row[0]) for row in csv.reader(f)]
    split = read(root / 'split.json')['test']
    require(type(split) is list and len(split) == 45 and all(type(i) is int and 1 <= i <= len(labels) for i in split) and len(set(split)) == len(split), 'invalid test split')
    results = []
    predictions = []
    for output in (original, repeated):
        with (output / 'predictions.csv').open(newline='') as f:
            reader = csv.DictReader(f)
            require(reader.fieldnames == ['row_id', 'y_true', 'model', 'dummy'], 'invalid prediction columns')
            rows = list(reader)
        require([int(r['row_id']) for r in rows] == split, 'prediction order differs from split')
        require(all(1 <= int(r['row_id']) <= len(labels) and int(r['y_true']) == labels[int(r['row_id'])-1] for r in rows), 'labels differ from data')
        predictions.append([(int(r['model']), int(r['dummy'])) for r in rows])
        all_metrics = {}
        for name in ('model', 'dummy'):
            matrix = [[0, 0, 0] for _ in range(3)]
            for row in rows:
                truth, pred = int(row['y_true']), int(row[name])
                require(truth in (1, 2, 3) and pred in (1, 2, 3), 'invalid class')
                matrix[truth-1][pred-1] += 1
            support = [sum(row) for row in matrix]
            require(all(support), 'empty class')
            correct = sum(matrix[i][i] for i in range(3))
            accuracy = Fraction(correct, len(rows))
            balanced = sum((Fraction(matrix[i][i], support[i]) for i in range(3)), Fraction()) / 3
            def score(value):
                return {'numerator': value.numerator, 'denominator': value.denominator, 'value': float(value)}
            all_metrics[name] = {'confusion_matrix': matrix, 'support': support, 'correct': correct, 'n': len(rows), 'accuracy': score(accuracy), 'balanced_accuracy': score(balanced)}
        require(strict_equal(all_metrics, read(output / 'metrics.json')), 'recomputed metrics differ')
        results.append(all_metrics)
    require(predictions[0] == predictions[1] and results[0] == results[1], 'source and repeat differ')
    return {'schema_version': 1, 'status': 'pass', 'test_rows': len(split), 'predictions_equal': True, 'scores': results[1]}


def cycle(binary, python, wheelhouse, scratch, evidence):
    binary, python, wheelhouse = [Path(x).resolve(strict=True) for x in (binary, python, wheelhouse)]
    scratch, evidence = Path(scratch).absolute(), Path(evidence).absolute()
    require(not scratch.exists() and not evidence.exists(), 'scratch and evidence must not exist')
    scratch.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
    source = scratch / 'source-wine'; shutil.copytree(PROJECT / 'examples/wine', source, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    store = scratch / 'store'; store.mkdir(mode=0o700)
    repo = uuid.uuid4().hex
    def cli(*args):
        return run([binary, *args], scratch)
    def command(action, typ, body, update=None):
        request = {'schema_version': 1, 'repository_id': repo, 'operation_id': uuid.uuid4().hex, 'type': typ, 'body': body, 'ref_update': update}
        path = scratch / ('request-' + request['operation_id'] + '.json'); save(path, request)
        return cli(*action, '--store', store, '--request', path), request
    def show(kind, rid):
        return cli(kind, 'show', '--store', store, '--id', rid)['record']
    def operation(op):
        return cli('operation', 'show', '--store', store, '--id', op)
    def put(path):
        data = path.read_bytes(); h = sha(data)
        cli('object', 'put', '--store', store, '--file', path, '--sha256', h, '--size', len(data))
        return {'algorithm': 'sha256', 'digest': h, 'size': len(data)}
    imported = run([sys.executable, HERE / 'import_reference.py', '--smlops', binary, '--store', store, '--repository-id', repo, '--wine-root', source, '--wheelhouse', wheelhouse], scratch)
    old = {k: imported[k]['record_id'] for k in ('dataset', 'snapshot', 'run')}
    original_records = {k: show(k, v) for k, v in old.items()}
    original_receipts = {k: operation(imported[k]['operation_id']) for k in old}
    old_body = original_records['snapshot']['body']
    require(old_body['parameters']['config.C'] == {'type': 'number', 'value': 1}, 'unexpected source parameters')
    initial_ref, _ = command(('ref', 'set'), 'set_reference', {'name': 'latest', 'target_kind': 'snapshot', 'target_id': old['snapshot'], 'expected_revision': 0})
    config = read(source / 'config.json'); config['C'] = 2.0
    config_path = scratch / 'control-config.json'; save(config_path, config); config_ref = put(config_path)
    manifest = read(source / 'manifest.json'); manifest['files']['config.json'] = config_ref['digest']
    manifest_path = scratch / 'control-manifest.json'; save(manifest_path, manifest); manifest_ref = put(manifest_path)
    control_body = copy.deepcopy(old_body); control_body['predecessor_id'] = old['snapshot']; control_body['parameters']['config.C'] = {'type': 'number', 'value': 2.0}
    for mat in control_body['materials']:
        if mat['name'] == 'config.json': mat['source']['object'] = config_ref
        if mat['name'] == 'manifest.json': mat['source']['object'] = manifest_ref
    control, _ = command(('snapshot', 'create'), 'save_snapshot', control_body, {'name': 'latest', 'expected_revision': 1})
    latest = cli('ref', 'show', '--store', store, '--name', 'latest')['reference']
    require(latest['target_id'] == control['record_id'] and latest['revision'] == 2, 'latest reference not updated')
    control_record = show('snapshot', control['record_id'])
    require(control_record['body']['predecessor_id'] == old['snapshot'] and control_record['body']['parameters']['config.C']['value'] == 2, 'control snapshot mismatch')
    control_materials = {m['name']: m for m in control_record['body']['materials']}
    require(control_materials['config.json']['source']['object'] == config_ref and control_materials['manifest.json']['source']['object'] == manifest_ref, 'control material mismatch')
    condition_diff = cli('diff', 'snapshot', '--store', store, '--left', old['snapshot'], '--right', control['record_id'])
    changes = {c['path']: c for c in condition_diff['changes']}
    require('/parameters/config.C' in changes and changes['/parameters/config.C']['right']['value'] == 2, 'control diff missing')
    plan = cli('recovery', 'plan', '--store', store, '--run', old['run'])
    require(plan['snapshot_id'] == old['snapshot'] and plan['conditions']['inputs'][0]['dataset_id'] == old['dataset'] and plan['conditions']['parameters']['config.C']['value'] == 1 and plan['stored_materials_ready'], 'plan is not historical')
    source_hashes = {m['path']: m['object']['digest'] for m in original_records['run']['body']['outputs']}
    source_files = {x['object']['digest']: (store / 'objects/sha256' / x['object']['digest']).read_bytes() for x in original_records['run']['body']['outputs']}
    require(all(sha(data) == h for h, data in source_files.items()), 'source output corrupt')
    shutil.rmtree(source)
    require(not source.exists(), 'source copy remains')
    workspace = scratch / 'repeat-work'
    success = run([sys.executable, HERE / 'repeat.py', '--smlops', binary, '--store', store, '--run', old['run'], '--python', python, '--work', workspace], scratch)
    require((success['materials'], success['runtime_preflight'], success['runtime'], success['execution'], success['registration'], success['numerical'], success['outcome']) == ('verified', 'passed', 'passed', 'succeeded', 'confirmed', 'pass', 'succeeded'), 'repeat not fully verified')
    new_run = show('run', success['run_id']); new_body = new_run['body']; new_request = read(workspace / 'register-request.json')
    require(success['run_id'] != old['run'] and new_body['snapshot_id'] == old['snapshot'] and new_body['source_run_id'] == old['run'] and new_request['operation_id'] != imported['run']['operation_id'], 'repeat provenance mismatch')
    result = oracle(workspace / 'inputs/wine', workspace / 'source-results', workspace / 'result')
    for classifier in ('model', 'dummy'):
        for metric in ('accuracy', 'balanced_accuracy'):
            require(new_body['metrics'][classifier + '.' + metric]['value'] == result['scores'][classifier][metric]['value'], 'catalog metric differs from oracle')
    require(operation(new_request['operation_id'])['record_id'] == success['run_id'], 'repeat receipt mismatch')
    for item in new_body['outputs']:
        data = (workspace / 'result' / Path(item['path']).name).read_bytes()
        require(sha(data) == item['object']['digest'] and len(data) == item['object']['size'], 'new output hash mismatch')
    run_diff = cli('diff', 'run', '--store', store, '--left', old['run'], '--right', success['run_id'])
    def history_intact():
        require(all(show(k, v) == original_records[k] for k, v in old.items()), 'old record changed')
        require(all(operation(imported[k]['operation_id']) == original_receipts[k] for k in old), 'old receipt changed')
        require(all((store / 'objects/sha256' / h).read_bytes() == data for h, data in source_files.items()), 'old output changed')
        require(cli('ref', 'show', '--store', store, '--name', 'latest')['reference'] == latest, 'latest reference changed')
    history_intact()
    seq_before = cli('repository', 'show', '--store', store)['sequence']
    data_ref = original_records['dataset']['body']['files'][0]['object']; data_path = store / 'objects/sha256' / data_ref['digest']; saved_data = data_path.read_bytes(); data_path.unlink()
    missing_plan = cli('recovery', 'plan', '--store', store, '--run', old['run'])
    require(not missing_plan['stored_materials_ready'] and any(b['code'] == 'missing' and b['material_key'].startswith('input:') for b in missing_plan['blockers']), 'missing input not reported')
    failure_work = scratch / 'missing-work'
    failed_proc = run([sys.executable, HERE / 'repeat.py', '--smlops', binary, '--store', store, '--run', old['run'], '--python', python, '--work', failure_work], scratch, expect=1)
    failed = json.loads(failed_proc.stdout); failed_record = show('run', failed['run_id']); failed_request = read(failure_work / 'register-request.json')
    require(failed['materials'] == 'failed' and failed['execution'] == 'unchecked' and failed['registration'] == 'confirmed' and failed['outcome'] == 'failed', 'missing input not safely failed')
    require(not (failure_work / 'result').exists() and failed_record['body']['started_at'] is None and failed_record['body']['finished_at'] is None, 'execution started on missing input')
    require(failed_record['body']['outcome'] == 'failed' and failed_record['body']['outputs'] == [] and failed_record['body']['metrics'] == {}, 'failed run records success evidence')
    require(operation(failed_request['operation_id'])['record_id'] == failed['run_id'], 'failed receipt mismatch')
    require(failed_record['body']['snapshot_id'] == old['snapshot'] and failed_record['body']['source_run_id'] == old['run'] and failed['run_id'] not in (old['run'], success['run_id']) and failed_request['operation_id'] not in (imported['run']['operation_id'], new_request['operation_id']), 'failed run provenance mismatch')
    require(cli('repository', 'show', '--store', store)['sequence'] == seq_before + 1, 'unexpected metadata mutation')
    history_intact()
    data_path.write_bytes(saved_data)
    restored_plan = cli('recovery', 'plan', '--store', store, '--run', old['run'])
    require(restored_plan['stored_materials_ready'] and any(m['purpose'] == 'input' and m['check'] == 'verified' for m in restored_plan['materials']), 'fixture input not restored')
    history_intact()
    # Store machine-readable evidence, never local work/store paths or wheels.
    for name, obj in {'import.json': imported, 'original-records.json': original_records, 'original-receipts.json': original_receipts, 'initial-ref-receipt.json': initial_ref, 'control-record.json': control_record, 'control-receipt.json': control, 'latest-ref.json': latest, 'conditions-diff.json': condition_diff, 'source-plan.json': plan, 'repeat-report.json': success, 'repeat-record.json': new_run, 'repeat-request.json': new_request, 'run-diff.json': run_diff, 'oracle.json': result, 'failure-report.json': failed, 'failure-record.json': failed_record, 'failure-request.json': failed_request, 'restored-plan.json': restored_plan, 'source-output-hashes.json': source_hashes}.items():
        save(evidence / name, obj)
    # Missing details can contain local paths; preserve structured status and redact detail only.
    for m in missing_plan['materials']:
        if m['detail'] is not None: m['detail'] = '[redacted diagnostic]'
    for b in missing_plan['blockers']:
        b['message'] = '[redacted diagnostic]'
    save(evidence / 'missing-plan.json', missing_plan)
    shutil.copyfile(config_path, evidence / 'control-config.json')
    shutil.copyfile(manifest_path, evidence / 'control-manifest.json')
    result_dir = evidence / 'repeat-output'; result_dir.mkdir()
    for name in NAMES: shutil.copyfile(workspace / 'result' / name, result_dir / name)
    hashes = {str(p.relative_to(evidence)): sha(p.read_bytes()) for p in sorted(evidence.rglob('*')) if p.is_file()}
    save(evidence / 'sha256.json', hashes)
    return {'status': 'pass', 'repository_id': repo, 'source_run_id': old['run'], 'repeat_run_id': success['run_id'], 'failed_run_id': failed['run_id'], 'oracle': result['status']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('smlops', 'python', 'wheelhouse', 'scratch', 'evidence'): parser.add_argument('--' + arg, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(cycle(args.smlops, args.python, args.wheelhouse, args.scratch, args.evidence), sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
