#!/usr/bin/env python3
"""Import the existing Wine reference into a local smlops catalog; no training."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

REVISION = '498bdefe3ee7d64db632910e3948c8d147738959'
REPOSITORY = 'https://github.com/m1ssha/scientific-mlops-core'
BUNDLE_REVISION = '2ecae3a2e2703fff651efa7d72e0e1b429670286'
PINNED = {
    'pipeline.py': '987c1c919830ba38f7caab79ccecf0c839e1d66823cd8ffb910f2094adcff4bf',
    'prepare_split.py': 'b6c697896fe59055ca8d52b1831e8950bbf73ca0ae15adf2b64404fd2d2bdb88',
    'protocol.json': '9057ec64676bcf39558955687af8e6425487a498e6f97de1b72145a966c681df',
    'manifest.json': '876fb9ee28e6e1501fa1ca32782e11786c3162ab7b1500f885d4a8554bc4f9b1',
}
REFERENCE_RECORD_SHA256 = '25c701b78a6ef4d6ebc4f9e927da4f4b1a3161b64ab9eab5d8c242f2e38e50c0'
PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_WINE = PROJECT / 'examples/wine'
OUTPUTS = ('result.json', 'predictions.csv', 'metrics.json', 'model.json', 'environment.json')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def typed(value):
    if value is None:
        return {'type': 'null'}
    typ = {bool: 'boolean', int: 'integer', float: 'number', str: 'string'}.get(type(value))
    if typ is None:
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        typ = 'string'
    return {'type': typ, 'value': value}


def utc(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError('reference time has no timezone')
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def preflight(root, reference, wheels):
    # Capture and hash a coherent bundle before executing any Wine code.
    pinned_bytes = {name: (root / name).read_bytes() for name in PINNED}
    for name, expected in PINNED.items():
        if hashlib.sha256(pinned_bytes[name]).hexdigest() != expected:
            raise ValueError('unsupported Wine v1 source')
    record_bytes = (reference / 'result.json').read_bytes()
    if hashlib.sha256(record_bytes).hexdigest() != REFERENCE_RECORD_SHA256:
        raise ValueError('unsupported reference record')
    manifest = json.loads(pinned_bytes['manifest.json'])
    record = json.loads(record_bytes)
    protocol = json.loads(pinned_bytes['protocol.json'])
    if protocol.get('freeze', {}).get('code_revision') != REVISION:
        raise ValueError('unexpected frozen revision')
    bundle_manifest = subprocess.run(['git', '-C', str(PROJECT), 'show', f'{BUNDLE_REVISION}:examples/wine/manifest.json'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    if bundle_manifest != pinned_bytes['manifest.json']:
        raise ValueError('manifest differs from bundle revision')
    for name in ('pipeline.py', 'prepare_split.py'):
        original = subprocess.run(['git', '-C', str(PROJECT), 'show', f'{REVISION}:examples/wine/{name}'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        if original != pinned_bytes[name]:
            raise ValueError('frozen code differs from git revision')
    bundle = {}
    for name, expected in manifest['files'].items():
        data = pinned_bytes[name] if name in pinned_bytes else (root / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('bundle file differs from manifest')
        bundle[name] = data
    if (reference.parent / 'input-manifest.json').read_bytes() != pinned_bytes['manifest.json']:
        raise ValueError('reference input manifest mismatch')
    output_bytes = {'result.json': record_bytes}
    for name, expected in record['outputs'].items():
        data = (reference / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('reference output hash mismatch')
        output_bytes[name] = data
    if set(output_bytes) != set(OUTPUTS):
        raise ValueError('unexpected reference outputs')
    with tempfile.TemporaryDirectory(prefix='wine-verified-') as temp:
        staged = Path(temp) / 'wine'
        out = Path(temp) / 'reference'
        out.mkdir()
        for name, data in {**bundle, 'manifest.json': pinned_bytes['manifest.json']}.items():
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for name, data in output_bytes.items():
            (out / name).write_bytes(data)
        module = types.ModuleType('trusted_wine_pipeline')
        module.__file__ = str(staged / 'pipeline.py')
        exec(compile(bundle['pipeline.py'], module.__file__, 'exec'), module.__dict__)
        rows, split, config = module.verify(staged)
        _, metrics, env, _ = module.validate_output(staged, out, rows, split)
        if record['phase'] != 'main':
            raise ValueError('reference is not a main run')
        locked = re.findall(r'--hash=sha256:([0-9a-f]{64})', bundle['requirements.lock'].decode())
        if len(locked) != 7 or len(set(locked)) != 7:
            raise ValueError('invalid wheel lock')
        wheel_files = {}
        if wheels is not None:
            module.wheelhouse(staged, wheels)
            paths = sorted(wheels.glob('*.whl'))
            if len(paths) != 7:
                raise ValueError('wheelhouse must contain exactly seven wheels')
            wheel_files = {digest(p): (p, p.stat().st_size) for p in paths}
            if set(wheel_files) != set(locked):
                raise ValueError('wheelhouse does not match lock')
    bundle_plan = {name: (manifest['files'][name], len(data)) for name, data in bundle.items()}
    output_plan = {name: (REFERENCE_RECORD_SHA256 if name == 'result.json' else record['outputs'][name], len(data)) for name, data in output_bytes.items()}
    return split, config, env, metrics, record, locked, wheel_files, bundle_plan, output_plan, len(pinned_bytes['manifest.json'])


class CLI:
    def __init__(self, binary, store, repo):
        self.binary, self.store, self.repo = binary, store, repo

    def call(self, *args, allow_not_initialized=False):
        proc = subprocess.run([str(self.binary), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode:
            try:
                code = json.loads(proc.stderr)['error']['code']
            except (ValueError, KeyError, TypeError):
                code = 'cli_error'
            if allow_not_initialized and code == 'not_initialized':
                return None
            raise ValueError('smlops failed: ' + code)
        return json.loads(proc.stdout)

    def put(self, path, expected, size):
        ref = {'algorithm': 'sha256', 'digest': expected, 'size': size}
        self.call('object', 'put', '--store', str(self.store), '--file', str(path), '--sha256', ref['digest'], '--size', str(ref['size']))
        return ref

    def operation_id(self, kind):
        return hashlib.sha256(('wine-reference-v1:' + self.repo + ':' + kind).encode()).hexdigest()[:32]

    def command(self, action, typ, body):
        command = {'schema_version': 1, 'repository_id': self.repo, 'operation_id': self.operation_id(typ), 'type': typ, 'body': body, 'ref_update': None}
        with tempfile.TemporaryDirectory(prefix='wine-import-') as temp:
            path = Path(temp) / 'command.json'
            path.write_text(json.dumps(command, allow_nan=False), encoding='utf-8')
            return self.call(*action, '--store', str(self.store), '--request', str(path))


def import_reference(binary, store, repo, root, reference, wheels):
    if not re.fullmatch(r'[0-9a-f]{32}', repo):
        raise ValueError('repository ID must contain 32 lowercase hexadecimal digits')
    split, config, env, metrics, record, locked, wheel_files, bundle_plan, output_plan, manifest_size = preflight(root, reference, wheels)
    cli = CLI(binary, store, repo)
    existing = cli.call('repository', 'show', '--store', str(store), allow_not_initialized=True)
    if existing is None:
        cli.call('repository', 'init', '--store', str(store), '--repository-id', repo)
    elif existing['repository_id'] != repo:
        raise ValueError('repository ID mismatch')
    refs = {name: cli.put(root / name, *bundle_plan[name]) for name in sorted(bundle_plan)}
    manifest_ref = cli.put(root / 'manifest.json', PINNED['manifest.json'], manifest_size)
    output_refs = {name: cli.put(reference / name, *output_plan[name]) for name in OUTPUTS}
    wheel_refs = {h: cli.put(p, h, size) for h, (p, size) in sorted(wheel_files.items())}
    dataset = cli.command(('dataset', 'publish'), 'publish_dataset', {'predecessor_id': None, 'files': [{'path': 'wine.data', 'object': refs['data/wine.data']}]})
    materials = []
    def material(name, purpose, path, source):
        materials.append({'name': name, 'purpose': purpose, 'required': True, 'path': path, 'source': source})
    def source(ref):
        return {'kind': 'object', 'object': ref}
    for name in sorted(bundle_plan):
        if name == 'data/wine.data':
            continue
        purpose = ('code' if name in ('pipeline.py', 'prepare_split.py', 'test_pipeline.py') else
                   'split' if name == 'split.json' else 'dependency' if name == 'requirements.lock' else 'configuration')
        material(name, purpose, 'wine/' + name, source(refs[name]))
    material('manifest.json', 'configuration', 'wine/manifest.json', source(manifest_ref))
    material('reference-environment', 'environment', 'wine/reference/environment.json', source(output_refs['environment.json']))
    for h in sorted(locked):
        if h in wheel_refs:
            filename = wheel_files[h][0].name
            src = source(wheel_refs[h])
        else:
            filename = h + '.whl'
            src = {'kind': 'external', 'locator': 'urn:sha256:' + h, 'expected_object': None}
        material('wheel:' + h, 'dependency', 'wine/wheels/' + filename, src)
    conditions = {k: typed(v) for k, v in sorted(env.items())}
    parameters = {'config.' + k: typed(v) for k, v in sorted(config.items())}
    parameters['split.seed'] = typed(split['strategy']['seed'])
    snapshot_body = {'predecessor_id': None, 'profile_id': 'wine-v1:sha256:' + PINNED['protocol.json'],
        'inputs': [{'role': 'data', 'dataset_id': dataset['record_id'], 'target_prefix': 'wine/data'}],
        'code': {'repository': REPOSITORY, 'commit': REVISION, 'entry_point': 'wine/pipeline.py'},
        'parameters': parameters, 'split': {'description': 'Wine v1 saved stratified split', 'materials': ['split.json']},
        'environment': {'description': 'Imported reference fingerprint; nested values use canonical JSON strings', 'materials': ['reference-environment', 'requirements.lock'] + ['wheel:' + h for h in sorted(locked)], 'conditions': conditions},
        'materials': materials}
    snap = cli.command(('snapshot', 'create'), 'save_snapshot', snapshot_body)
    metric_values = {}
    for classifier in ('model', 'dummy'):
        for metric in ('accuracy', 'balanced_accuracy'):
            definition = ('correct predictions / test count' if metric == 'accuracy' else 'mean per-class recall across Wine classes 1, 2, 3')
            metric_values[classifier + '.' + metric] = {'definition': 'Wine v1 ' + classifier + ': ' + definition, 'unit': 'ratio', 'value': metrics[classifier][metric]['value']}
    observations = {k: {'status': 'passed', 'value': v, 'evidence': 'Imported reference environment.json; caller-reported, not a current environment check'} for k, v in conditions.items()}
    run_body = {'snapshot_id': snap['record_id'], 'source_run_id': None, 'started_at': utc(record['started_at']), 'finished_at': utc(record['finished_at']), 'outcome': 'succeeded', 'reason': None, 'observations': observations, 'metrics': metric_values, 'outputs': [{'path': 'reference/' + name, 'role': name, 'media_type': 'text/csv' if name.endswith('.csv') else 'application/json', 'object': output_refs[name]} for name in OUTPUTS], 'evidence_source': 'caller_reported'}
    run = cli.command(('run', 'register'), 'register_run', run_body)
    return {'schema_version': 1, 'repository_id': repo, 'dataset': dataset, 'snapshot': snap, 'run': run, 'wheelhouse': 'verified' if wheels else 'unchecked', 'provenance': {'description': 'imported caller-reported Wine reference', 'original_run_id': record['run_id'], 'phase': record['phase'], 'bundle_revision': BUNDLE_REVISION, 'code_revision': REVISION}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smlops', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--repository-id', required=True)
    parser.add_argument('--wine-root', type=Path, default=DEFAULT_WINE)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--wheelhouse', type=Path)
    args = parser.parse_args()
    try:
        result = import_reference(args.smlops.resolve(), args.store.resolve(), args.repository_id, args.wine_root.resolve(), (args.reference or args.wine_root / 'evidence/main-v1/reference/result').resolve(), args.wheelhouse.resolve() if args.wheelhouse else None)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        # Avoid exposing local filesystem paths in machine-readable output.
        print(json.dumps({'error': {'code': 'import_failed', 'message': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
