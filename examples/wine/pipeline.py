#!/usr/bin/env python3
"""Wine example: explicit bundle validation, execution and independent comparison."""
import os
THREADS = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'BLIS_NUM_THREADS')
for name in THREADS:
    os.environ[name] = '1'
import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import re
import sys
import uuid
import warnings
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

FILES = ('data/wine.data', 'data/SOURCE.md', 'requirements.lock', 'split.json', 'config.json',
         'protocol.json', 'pipeline.py', 'prepare_split.py', 'README.md', 'test_pipeline.py')
PACKAGES = ('cloudpickle', 'joblib', 'narwhals', 'numpy', 'scikit-learn', 'scipy', 'threadpoolctl')
CLASSES = (1, 2, 3)
LOADED_SOURCE_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PROTOCOL_FIELDS = {'version', 'status', 'purpose', 'data', 'split', 'method', 'runtime',
                   'scores', 'comparison', 'pilots', 'main', 'limits', 'control', 'freeze'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('non-finite JSON')))


def write(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')


def manifest(root):
    return {'schema_version': 1, 'files': {name: sha(root / name) for name in FILES}}


def verify(root):
    m = read(root / 'manifest.json')
    if m != manifest(root):
        raise ValueError('manifest mismatch')
    rows = []
    with (root / 'data/wine.data').open(newline='') as f:
        for row in csv.reader(f):
            if len(row) != 14:
                raise ValueError('invalid data row')
            label, *features = row
            values = [float(x) for x in features]
            if int(label) not in CLASSES or not all(math.isfinite(x) for x in values):
                raise ValueError('invalid data')
            rows.append((int(label), values))
    split = read(root / 'split.json')
    if split['dataset_sha256'] != sha(root / 'data/wine.data'):
        raise ValueError('split dataset mismatch')
    train, test = split['train'], split['test']
    ids = train + test
    if (len(rows) != 178 or len(train) != 133 or len(test) != 45 or
        any(type(i) is not int for i in ids) or sorted(ids) != list(range(1, 179))):
        raise ValueError('invalid split membership')
    if [sum(rows[i-1][0] == c for i in test) for c in CLASSES] != [15, 18, 12]:
        raise ValueError('invalid split supports')
    config = read(root / 'config.json')
    expected = {'solver': 'lbfgs', 'tol': 0.0001, 'max_iter': 1000,
                'fit_intercept': True, 'l1_ratio': 0.0}
    if (type(config) is not dict or set(config) != set(expected) | {'C'} or
        any(type(config[k]) is not type(v) or config[k] != v for k, v in expected.items()) or
        type(config['C']) not in (int, float) or not math.isfinite(config['C']) or not 0 < config['C'] <= 2):
        raise ValueError('unsupported configuration')
    protocol = read(root / 'protocol.json')
    if (type(protocol) is not dict or set(protocol) != PROTOCOL_FIELDS or
        type(protocol['version']) is not int or protocol['version'] != 1 or protocol['status'] not in ('draft', 'frozen') or
        any(type(protocol[k]) is not str or not protocol[k].strip() for k in PROTOCOL_FIELDS - {'version', 'status', 'freeze'})):
        raise ValueError('invalid protocol')
    freeze = protocol['freeze']
    if protocol['status'] == 'frozen':
        if (type(freeze) is not dict or set(freeze) != {'code_revision', 'code_files', 'frozen_at'} or
            type(freeze['code_revision']) is not str or not re.fullmatch('[0-9a-f]{40}', freeze['code_revision']) or
            freeze['code_files'] != {name: sha(root / name) for name in ('pipeline.py', 'prepare_split.py')}):
            raise ValueError('invalid frozen code identity')
        try:
            frozen_at = datetime.fromisoformat(freeze['frozen_at'])
            if frozen_at.tzinfo is None or frozen_at.utcoffset().total_seconds() != 0:
                raise ValueError('freeze time must be UTC')
        except (ValueError, TypeError, AttributeError) as e:
            raise ValueError('invalid freeze time') from e
    elif freeze is not None:
        raise ValueError('draft protocol must not claim frozen code')
    return rows, split, config


def metrics(y, predictions):
    if len(y) != len(predictions) or not y:
        raise ValueError('prediction length')
    matrix = [[0] * 3 for _ in CLASSES]
    for actual, pred in zip(y, predictions):
        if actual not in CLASSES or pred not in CLASSES:
            raise ValueError('invalid class')
        matrix[actual-1][pred-1] += 1
    support = [sum(row) for row in matrix]
    if not all(support):
        raise ValueError('zero support')
    correct = sum(matrix[i][i] for i in range(3))
    accuracy = Fraction(correct, len(y))
    balanced = sum((Fraction(matrix[i][i], support[i]) for i in range(3)), Fraction()) / 3
    def score(frac):
        return {'numerator': frac.numerator, 'denominator': frac.denominator, 'value': float(frac)}
    return {'confusion_matrix': matrix, 'support': support, 'correct': correct, 'n': len(y),
            'accuracy': score(accuracy), 'balanced_accuracy': score(balanced)}


def environment(root):
    from threadpoolctl import threadpool_info
    pools = []
    for p in threadpool_info():
        path = p.get('filepath')
        item = {k: p.get(k) for k in ('internal_api', 'version', 'threading_layer', 'num_threads', 'architecture')}
        if not path or not item['internal_api'] or item['num_threads'] != 1:
            raise ValueError('unknown or unconstrained numerical backend')
        if item['internal_api'] in ('openblas', 'mkl', 'blis') and not item['version']:
            raise ValueError('missing BLAS version')
        item.update(basename=Path(path).name, sha256=sha(Path(path)))
        pools.append(item)
    if not pools:
        raise ValueError('no numerical backends detected')
    libc = platform.libc_ver()
    if not all(libc):
        raise ValueError('unknown libc')
    result = {'python_implementation': platform.python_implementation(), 'python_version': platform.python_version(),
            'system': platform.system(), 'machine': platform.machine(), 'libc': list(libc),
            'packages': {p: importlib.metadata.version(p) for p in PACKAGES},
            'lock_sha256': sha(root / 'requirements.lock'), 'threads': {x: os.environ[x] for x in THREADS},
            'backends': sorted(pools, key=lambda x: json.dumps(x, sort_keys=True))}
    validate_environment(result, root)
    return result


def wheelhouse(root, directory):
    locked = re.findall(r'--hash=sha256:([0-9a-f]{64})', (root / 'requirements.lock').read_text())
    found = {sha(p) for p in directory.glob('*.whl') if p.is_file()}
    if len(locked) != 7 or not set(locked) <= found:
        raise ValueError('wheelhouse missing locked wheel(s)')
    return 'verified'


def train(rows, split, config):
    import numpy as np
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.dummy import DummyClassifier
    from sklearn.exceptions import ConvergenceWarning
    train_ids, test_ids = split['train'], split['test']
    x = np.array([r[1] for r in rows]); y = np.array([r[0] for r in rows])
    tr = np.array(train_ids) - 1; te = np.array(test_ids) - 1
    model = Pipeline([('scaler', StandardScaler()), ('model', LogisticRegression(**config))])
    dummy = DummyClassifier(strategy='most_frequent')
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        model.fit(x[tr], y[tr]); dummy.fit(x[tr], y[tr])
    scaler, fitted = model.named_steps['scaler'], model.named_steps['model']
    parameters = {'scaler': {'mean': scaler.mean_.tolist(), 'scale': scaler.scale_.tolist(), 'var': scaler.var_.tolist()},
                  'model': {'classes': fitted.classes_.tolist(), 'coef': fitted.coef_.tolist(),
                            'intercept': fitted.intercept_.tolist(), 'n_iter': fitted.n_iter_.tolist()}}
    return y[te].tolist(), model.predict(x[te]).tolist(), dummy.predict(x[te]).tolist(), parameters


def error_text(e):
    if isinstance(e, OSError):
        return type(e).__name__ + ': ' + (e.strerror or 'filesystem error')
    return type(e).__name__ + ': ' + str(e)


def now():
    return datetime.now(timezone.utc).isoformat()


def run(root, out, phase='validation'):
    out.mkdir(parents=True, exist_ok=False)
    start = now(); run_id = str(uuid.uuid4())
    try:
        if Path(__file__).resolve() != (root / 'pipeline.py').resolve() or LOADED_SOURCE_SHA != sha(root / 'pipeline.py'):
            raise ValueError('active source is not the verified bundle source')
        if phase not in ('pilot', 'validation', 'main'):
            raise ValueError('invalid phase')
        initial_manifest = manifest(root)
        initial_manifest_hash = sha(root / 'manifest.json')
        rows, split, config = verify(root)
        if manifest(root) != initial_manifest or sha(root / 'manifest.json') != initial_manifest_hash:
            raise ValueError('inputs changed during validation')
        if phase in ('pilot', 'main') and config['C'] != 1.0:
            raise ValueError('pilot and main require canonical configuration')
        if phase == 'main' and read(root / 'protocol.json')['status'] != 'frozen':
            raise ValueError('main phase requires frozen protocol')
        y, pred, dummy, params = train(rows, split, config)
        env = environment(root)
        with (out / 'predictions.csv').open('w', newline='') as f:
            writer = csv.writer(f); writer.writerow(['row_id', 'y_true', 'model', 'dummy'])
            writer.writerows(zip(split['test'], y, pred, dummy))
        write(out / 'metrics.json', {'model': metrics(y, pred), 'dummy': metrics(y, dummy)})
        write(out / 'model.json', params); write(out / 'environment.json', env)
        if manifest(root) != initial_manifest or sha(root / 'manifest.json') != initial_manifest_hash:
            raise ValueError('inputs changed during execution')
        result = {'status': 'success', 'run_id': run_id, 'phase': phase, 'started_at': start, 'finished_at': now(),
                  'manifest_sha256': initial_manifest_hash, 'inputs': initial_manifest['files'],
                  'outputs': {name: sha(out / name) for name in ('predictions.csv', 'metrics.json', 'model.json', 'environment.json')}}
        write(out / 'result.json', result)
        return result
    except Exception as e:
        result = {'status': 'failed', 'run_id': run_id, 'phase': phase, 'started_at': start, 'finished_at': now(),
                  'error': error_text(e)}
        write(out / 'result.json', result)
        raise


def strict_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(strict_equal(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(strict_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def valid_text(value):
    return type(value) is str and bool(value.strip()) and value.strip().lower() not in ('unknown', 'none', 'n/a')


def validate_model(model):
    if type(model) is not dict or set(model) != {'scaler', 'model'}:
        raise ValueError('invalid model structure')
    scaler, classifier = model['scaler'], model['model']
    if type(scaler) is not dict or set(scaler) != {'mean', 'scale', 'var'} or type(classifier) is not dict or set(classifier) != {'classes', 'coef', 'intercept', 'n_iter'}:
        raise ValueError('invalid model structure')
    def numbers(values, length):
        return type(values) is list and len(values) == length and all(type(v) in (int, float) and math.isfinite(v) for v in values)
    if (not all(numbers(scaler[k], 13) for k in scaler) or
        any(v <= 0 for v in scaler['scale']) or any(v < 0 for v in scaler['var']) or
        not strict_equal(classifier['classes'], [1, 2, 3]) or
        type(classifier['coef']) is not list or len(classifier['coef']) != 3 or
        not all(numbers(row, 13) for row in classifier['coef']) or
        not numbers(classifier['intercept'], 3) or
        type(classifier['n_iter']) is not list or len(classifier['n_iter']) != 1 or
        any(type(i) is not int or not 0 < i <= 1000 for i in classifier['n_iter'])):
        raise ValueError('invalid model values')


def validate_record(record, root):
    if (type(record) is not dict or set(record) != {'status', 'run_id', 'phase', 'started_at', 'finished_at', 'manifest_sha256', 'inputs', 'outputs'} or record.get('status') != 'success'):
        raise ValueError('execution record is not successful')
    try:
        if str(uuid.UUID(record['run_id'])) != record['run_id']:
            raise ValueError('invalid run ID')
        start = datetime.fromisoformat(record['started_at']); end = datetime.fromisoformat(record['finished_at'])
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError('invalid timestamps')
    except (ValueError, TypeError, AttributeError, KeyError) as e:
        raise ValueError('invalid execution identity or timestamps') from e
    if (record.get('phase') not in ('pilot', 'validation', 'main') or
        (record['phase'] in ('pilot', 'main') and read(root / 'config.json')['C'] != 1.0) or
        (record['phase'] == 'main' and read(root / 'protocol.json')['status'] != 'frozen') or
        record.get('manifest_sha256') != sha(root / 'manifest.json') or record.get('inputs') != manifest(root)['files']):
        raise ValueError('execution or input record mismatch')


def validate_output(root, out, rows, split):
    record = read(out / 'result.json')
    validate_record(record, root)
    expected = ('predictions.csv', 'metrics.json', 'model.json', 'environment.json')
    if type(record.get('outputs')) is not dict or set(record['outputs']) != set(expected) or any(sha(out / p) != record['outputs'][p] for p in expected):
        raise ValueError('output hash mismatch')
    with (out / 'predictions.csv').open(newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ['row_id', 'y_true', 'model', 'dummy']:
            raise ValueError('invalid prediction header')
        data = list(reader)
    ids = [int(r['row_id']) for r in data]
    truth = [rows[i-1][0] for i in split['test']]
    if ids != split['test'] or [int(r['y_true']) for r in data] != truth:
        raise ValueError('prediction identity or truth mismatch')
    preds = {key: [int(r[key]) for r in data] for key in ('model', 'dummy')}
    recomputed = {key: metrics(truth, value) for key, value in preds.items()}
    if not strict_equal(read(out / 'metrics.json'), recomputed):
        raise ValueError('metric mismatch')
    validate_model(read(out / 'model.json'))
    env = read(out / 'environment.json')
    try:
        validate_environment(env, root)
    except Exception as e:
        raise InvalidEnvironment(error_text(e)) from e
    return preds, recomputed, env, record['run_id']


def lock_versions(root):
    versions = {}
    for line in (root / 'requirements.lock').read_text().splitlines():
        if not line or line.startswith('#') or line.startswith('--'):
            continue
        match = re.fullmatch(r'([a-zA-Z0-9-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})', line)
        if not match or match[1] in versions:
            raise ValueError('invalid lock format')
        versions[match[1]] = match[2]
    if set(versions) != set(PACKAGES):
        raise ValueError('invalid lock packages')
    return versions


class InvalidEnvironment(ValueError):
    pass


def validate_environment(env, root):
    if type(env) is not dict or set(env) != {'python_implementation', 'python_version', 'system', 'machine', 'libc', 'packages', 'lock_sha256', 'threads', 'backends'}:
        raise ValueError('invalid environment schema')
    if (env['python_implementation'], env['python_version'], env['system'], env['machine']) != ('CPython', '3.12.15', 'Linux', 'x86_64'):
        raise ValueError('unsupported environment profile')
    if (type(env['libc']) is not list or len(env['libc']) != 2 or not all(valid_text(v) for v in env['libc']) or
        type(env['packages']) is not dict or env['packages'] != lock_versions(root) or
        env['lock_sha256'] != sha(root / 'requirements.lock') or
        type(env['threads']) is not dict or env['threads'] != {x: '1' for x in THREADS} or
        type(env['backends']) is not list or not env['backends']):
        raise ValueError('incomplete environment')
    for p in env['backends']:
        if (type(p) is not dict or set(p) != {'internal_api', 'version', 'threading_layer', 'num_threads', 'architecture', 'basename', 'sha256'} or
            p['internal_api'] not in ('openblas', 'mkl', 'blis', 'openmp') or type(p['num_threads']) is not int or p['num_threads'] != 1 or
            not valid_text(p['basename']) or Path(p['basename']).name != p['basename'] or
            type(p['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', p['sha256'])):
            raise ValueError('invalid backend')
        if p['internal_api'] == 'openmp':
            if any(v is not None and not valid_text(v) for v in (p['version'], p['threading_layer'], p['architecture'])):
                raise ValueError('invalid OpenMP fields')
        elif not all(valid_text(p[k]) for k in ('version', 'threading_layer', 'architecture')):
            raise ValueError('incomplete BLAS fields')
    if not any(p['internal_api'] in ('openblas', 'mkl', 'blis') for p in env['backends']):
        raise ValueError('no BLAS backend')
    if env['backends'] != sorted(env['backends'], key=lambda x: json.dumps(x, sort_keys=True)):
        raise ValueError('unsorted backends')


def compare(root_a, a, root_b, b):
    result = {'materials': 'unchecked', 'conditions': 'unchecked', 'execution': 'unchecked', 'numerical': 'unchecked'}
    try:
        rows_a, split_a, _ = verify(root_a); rows_b, split_b, _ = verify(root_b)
        result['materials'] = 'equal' if manifest(root_a) == manifest(root_b) else 'different'
    except Exception as e:
        result['materials'] = 'invalid'; result['error'] = error_text(e); return result
    try:
        for out in (a, b):
            record = read(out / 'result.json')
            if record.get('status') == 'failed':
                result['execution'] = 'failed'
                result['error'] = 'execution record reports failure'
                return result
        pa, ma, ea, ida = validate_output(root_a, a, rows_a, split_a)
        pb, mb, eb, idb = validate_output(root_b, b, rows_b, split_b)
        if ida == idb:
            raise ValueError('repeat must have a distinct run_id')
        result['execution'] = 'valid'
    except InvalidEnvironment as e:
        result['execution'] = 'invalid'; result['conditions'] = 'invalid'; result['error'] = error_text(e); return result
    except Exception as e:
        result['execution'] = 'invalid'; result['error'] = error_text(e); return result
    result['conditions'] = 'equal' if ea == eb else 'different'
    if result['materials'] == 'equal' and result['conditions'] == 'equal':
        result['numerical'] = 'pass' if (pa, ma) == (pb, mb) else 'different'
    else:
        result['numerical'] = 'incomparable'
    return result


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='command', required=True)
    for name in ('manifest', 'verify', 'run'):
        p = sub.add_parser(name); p.add_argument('--root', type=Path, required=True)
        if name == 'run':
            p.add_argument('--out', type=Path, required=True)
            p.add_argument('--phase', choices=('pilot', 'validation', 'main'), default='validation')
        if name == 'verify': p.add_argument('--wheelhouse', type=Path)
    p = sub.add_parser('compare'); p.add_argument('--root-a', type=Path, required=True); p.add_argument('--a', type=Path, required=True); p.add_argument('--root-b', type=Path, required=True); p.add_argument('--b', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'manifest':
            write(args.root / 'manifest.json', manifest(args.root)); result = {'status': 'success'}
        elif args.command == 'verify':
            verify(args.root); result = {'status': 'success', 'materials': 'verified', 'wheelhouse': wheelhouse(args.root, args.wheelhouse) if args.wheelhouse else 'unchecked'}
        elif args.command == 'run': result = run(args.root, args.out, args.phase)
        else: result = compare(args.root_a, args.a, args.root_b, args.b)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result.get('status') == 'success' or result.get('numerical') == 'pass' else 1
    except Exception as e:
        print(json.dumps({'status': 'failed', 'error': error_text(e)})); return 1

if __name__ == '__main__':
    sys.exit(main())
