#!/usr/bin/env python3
"""Repeat one pinned Wine reference with offline verified materials."""
import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from import_reference import PINNED, REVISION, REPOSITORY, OUTPUTS, REFERENCE_RECORD_SHA256, typed, utc

HELPER = Path(__file__).with_name('repeat_helper.py').resolve()
ENV_KEYS = {'python_implementation', 'python_version', 'system', 'machine', 'libc', 'packages', 'lock_sha256', 'threads', 'backends'}
THREADS = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'BLIS_NUM_THREADS')

class Interrupted(Exception):
    pass

def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def execute(argv, env=None, on_start=None):
    proc = subprocess.Popen([str(x) for x in argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=True)
    try:
        if on_start is not None:
            on_start()
        out, err = proc.communicate()
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise
    if proc.returncode:
        try:
            detail = json.loads(err).get('error')
        except (ValueError, AttributeError):
            detail = None
        if detail in ('runtime fingerprint differs from reference', 'output fingerprint differs from reference'):
            raise ValueError(detail)
        raise ValueError('process failed with exit code ' + str(proc.returncode))
    return out

def cli(binary, *args):
    return json.loads(execute([binary, *args]))

def clean_env():
    # Ignore user Python/pip settings, caches and network index configuration.
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'PIP_CONFIG_FILE': os.devnull,
           'PIP_NO_INDEX': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
           'PIP_NO_CACHE_DIR': '1', 'PIP_NO_INPUT': '1'}
    env.update({k: '1' for k in THREADS})
    return env

def safe_path(root, path):
    p = PurePosixPath(path)
    if not path or p.is_absolute() or any(x in ('', '.', '..') for x in path.split('/')):
        raise ValueError('unsafe material path')
    current = root
    for name in p.parts[:-1]:
        current = current / name
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            if current.is_symlink() or not current.is_dir():
                raise ValueError('unsafe directory')
    target = current / p.parts[-1]
    if target.exists() or target.is_symlink():
        raise ValueError('material target exists')
    return target

def materialize(binary, store, root, material):
    ref = material['source']['object']
    if ref['algorithm'] != 'sha256' or not re.fullmatch('[0-9a-f]{64}', ref['digest']) or type(ref['size']) is not int or ref['size'] < 0:
        raise ValueError('invalid object reference')
    target = safe_path(root, material['path'])
    cli(binary, 'object', 'get', '--store', store, '--sha256', ref['digest'], '--size', str(ref['size']), '--output', target)
    # object get verifies before copying; independently hash the actual materialized bytes.
    h = hashlib.sha256(); size = 0
    with target.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk); size += len(chunk)
    if size != ref['size'] or h.hexdigest() != ref['digest']:
        raise ValueError('material changed during copy')
    return target

def validate_plan(plan):
    conditions = plan['conditions']
    if conditions['profile_id'] != 'wine-v1:sha256:' + PINNED['protocol.json'] or conditions['code'] != {'repository': REPOSITORY, 'commit': REVISION, 'entry_point': 'wine/pipeline.py'}:
        raise ValueError('unsupported Wine profile')
    mats = plan['materials']
    required = [m for m in mats if m['purpose'] != 'original_output']
    paths = [m['path'] for m in required]
    if len(paths) != len(set(paths)) or any(not p.startswith('wine/') for p in paths):
        raise ValueError('invalid input mapping')
    by_path = {m['path']: m for m in required}
    expected = {'wine/data/wine.data', 'wine/reference/environment.json'} | {'wine/' + p for p in PINNED}
    if not expected <= set(paths) or any(not m['required'] or m['source']['kind'] != 'object' for m in required):
        raise ValueError('incomplete Wine inputs')
    for name, digest in PINNED.items():
        if by_path['wine/' + name]['source']['object']['digest'] != digest:
            raise ValueError('unapproved Wine bytes')
    wheels = [m for m in required if m['path'].startswith('wine/wheels/')]
    if len(wheels) != 7 or any(not re.fullmatch(r'wine/wheels/[^/]+\.whl', m['path']) for m in wheels):
        raise ValueError('invalid wheels')
    if set(conditions['environment']['conditions']) != ENV_KEYS:
        raise ValueError('incomplete environment conditions')
    outputs = [m for m in mats if m['purpose'] == 'original_output']
    if any(m['path'] not in {'reference/' + n for n in OUTPUTS} for m in outputs):
        raise ValueError('unsupported source output')
    return required, outputs

def report_write(path, report):
    path.write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')

def repeat(binary, store, source, python, work):
    work = Path(work).absolute()
    for parent in reversed(work.parents):
        if parent.is_symlink():
            raise ValueError('symlink in work path')
    work.mkdir(mode=0o700, parents=False, exist_ok=False)
    binary, store, python = map(lambda x: Path(x).absolute(), (binary, store, python))
    report = {'schema_version': 1, 'source_run_id': source, 'snapshot_id': None, 'run_id': None,
              'materials': 'unchecked', 'runtime': 'unchecked', 'execution': 'unchecked',
              'registration': 'unchecked', 'numerical': 'unchecked', 'error': None}
    plan = None; observations = {}; started = finished = None; outcome = 'failed'; metrics = {}; outputs = []; reason = None
    try:
        plan = cli(binary, 'recovery', 'plan', '--store', store, '--run', source)
        report['snapshot_id'] = plan['snapshot_id']; report['view_sequence'] = plan['view_sequence']
        report['material_checks'] = [{'key': m['key'], 'check': m['check'], 'required': m['required']} for m in plan['materials']]
        report['blockers'] = [{'material_key': b['material_key'], 'code': b['code']} for b in plan['blockers']]
        required, original = validate_plan(plan)
        if not plan['stored_materials_ready']:
            report['materials'] = 'failed'; raise ValueError('required materials unavailable')
        inputs = work / 'inputs'; inputs.mkdir(mode=0o700)
        refs = work / 'source-results'; refs.mkdir(mode=0o700)
        for m in required:
            materialize(binary, store, inputs, m)
        have_original = (len(original) == len(OUTPUTS) and all(m['check'] == 'verified' for m in original)
                         and any(m['path'] == 'reference/result.json' and m['source'].get('object', {}).get('digest') == REFERENCE_RECORD_SHA256 for m in original))
        if have_original:
            for m in original:
                try:
                    copy = dict(m); copy['path'] = PurePosixPath(m['path']).name
                    materialize(binary, store, refs, copy)
                except (ValueError, OSError):
                    have_original = False; break
        report['materials'] = 'verified'
        root = inputs / 'wine'; source_env = root / 'reference/environment.json'
        expected_env = json.loads(source_env.read_text())
        expected_conditions = {k: typed(v) for k, v in expected_env.items()}
        if expected_conditions != plan['conditions']['environment']['conditions']:
            raise ValueError('reference fingerprint differs from snapshot conditions')
        # Verify all expected manifest files and lock wheel digests before pip runs.
        manifest = json.loads((root / 'manifest.json').read_text())
        actual_paths = {str(m['path'])[5:] for m in required if not m['path'].startswith(('wine/wheels/', 'wine/reference/'))}
        if actual_paths != set(manifest['files']) | {'manifest.json'}:
            raise ValueError('unsupported bundle mapping')
        for name, digest in manifest['files'].items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                raise ValueError('bundle manifest mismatch')
        lock = (root / 'requirements.lock').read_text()
        hashes = re.findall(r'--hash=sha256:([0-9a-f]{64})', lock)
        wheel_paths = list((root / 'wheels').iterdir())
        if len(hashes) != 7 or len(set(hashes)) != 7 or {hashlib.sha256(p.read_bytes()).hexdigest() for p in wheel_paths} != set(hashes):
            raise ValueError('wheel lock mismatch')
        env = clean_env()
        base = json.loads(execute([python, '-I', '-c', 'import json,platform; print(json.dumps([platform.python_implementation(),platform.python_version(),platform.system(),platform.machine()]))'], env))
        if base != ['CPython', '3.12.15', 'Linux', 'x86_64']:
            raise ValueError('unsupported base Python or OS')
        venv = work / 'venv'; execute([python, '-I', '-m', 'venv', venv], env)
        py = venv / 'bin/python'
        execute([py, '-I', '-m', 'pip', 'install', '--no-index', '--no-cache-dir', '--require-hashes', '--no-deps', '--only-binary=:all:', '--find-links', root / 'wheels', '-r', root / 'requirements.lock'], env)
        pre = json.loads(execute([py, '-I', HELPER, 'preflight', root, source_env], env))
        params = {'config.' + k: typed(v) for k, v in pre['config'].items()}; params['split.seed'] = typed(pre['seed'])
        if params != plan['conditions']['parameters']:
            raise ValueError('parameters differ from snapshot')
        observations = {k: {'status':'passed', 'value':v, 'evidence':'Wine preflight fingerprint verified'} for k,v in expected_conditions.items()}
        report['runtime'] = 'passed'
        # Check the materialized bytes again at the point of use, after setup.
        for m in required:
            try:
                path = inputs / m['path']
                if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != inputs.parent):
                    raise ValueError('material path changed')
                data = path.read_bytes(); ref = m['source']['object']
                if len(data) != ref['size'] or hashlib.sha256(data).hexdigest() != ref['digest']:
                    raise ValueError('material changed before execution')
            except (OSError, ValueError):
                report['materials'] = 'failed'
                raise
        def mark_started():
            nonlocal started
            started = now()
        execute([py, '-I', root / 'pipeline.py', 'run', '--phase', 'validation', '--root', root, '--out', work / 'result'], env, on_start=mark_started)
        finished = now()
        validated = json.loads(execute([py, '-I', HELPER, 'validate', root, source_env, work / 'result', refs if have_original else work / 'missing-original'], env))
        record = validated['record']; started = utc(record['started_at']); finished = utc(record['finished_at'])
        report['execution'] = 'succeeded'; report['numerical'] = validated['comparison']['numerical']
        for classifier in ('model', 'dummy'):
            for metric in ('accuracy', 'balanced_accuracy'):
                definition = ('correct predictions / test count' if metric == 'accuracy' else 'mean per-class recall across Wine classes 1, 2, 3')
                metrics[classifier + '.' + metric] = {'definition': 'Wine v1 ' + classifier + ': ' + definition, 'unit': 'ratio', 'value': validated['metrics'][classifier][metric]['value']}
        for name in OUTPUTS:
            path = work / 'result' / name; data = path.read_bytes(); digest = hashlib.sha256(data).hexdigest()
            cli(binary, 'object', 'put', '--store', store, '--file', path, '--sha256', digest, '--size', str(len(data)))
            outputs.append({'path':'repeat/' + name, 'role':name, 'media_type':'text/csv' if name.endswith('.csv') else 'application/json', 'object':{'algorithm':'sha256', 'digest':digest, 'size':len(data)}})
        outcome = 'succeeded'
    except Interrupted:
        outcome = 'interrupted'; reason = 'interrupted by signal'; report['execution'] = 'interrupted' if started else 'unchecked'; report['error'] = reason
        if started: finished = now()
    except Exception as exc:
        reason = type(exc).__name__ if isinstance(exc, OSError) else str(exc); report['error'] = reason
        if started:
            if report['execution'] != 'succeeded':
                report['execution'] = 'failed'
            if finished is None: finished = now()
        elif report['materials'] == 'verified': report['runtime'] = 'failed'
        elif plan is not None: report['materials'] = 'failed'
    if plan is not None:
        body = {'snapshot_id':plan['snapshot_id'], 'source_run_id':source, 'started_at':started, 'finished_at':finished,
                'outcome':outcome, 'reason':None if outcome == 'succeeded' else (reason or 'repeat failed'),
                'observations':observations, 'metrics':metrics if outcome == 'succeeded' else {},
                'outputs':outputs if outcome == 'succeeded' else [], 'evidence_source':'caller_reported'}
        request = {'schema_version':1, 'repository_id':plan['repository_id'], 'operation_id':uuid.uuid4().hex,
                   'type':'register_run', 'body':body, 'ref_update':None}
        request_path = work / 'register-request.json'
        request_path.write_text(json.dumps(request, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
        try:
            receipt = cli(binary, 'run', 'register', '--store', store, '--request', request_path)
            report['run_id'] = receipt['record_id']; report['registration'] = 'confirmed'
        except Exception as exc:
            report['registration'] = 'unconfirmed'; report['error'] = (report['error'] or '') + ' registration: ' + (type(exc).__name__ if isinstance(exc, OSError) else str(exc))
    report_write(work / 'report.json', report)
    return report

def exit_status(report):
    return 0 if (report['materials'], report['runtime'], report['execution'], report['registration'], report['numerical']) == ('verified', 'passed', 'succeeded', 'confirmed', 'pass') else 1

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('smlops', 'store', 'run', 'python', 'work'):
        parser.add_argument('--' + arg, required=True)
    args = parser.parse_args()
    def interrupt(signum, frame):
        raise Interrupted()
    signal.signal(signal.SIGINT, interrupt); signal.signal(signal.SIGTERM, interrupt)
    try:
        report = repeat(args.smlops, args.store, args.run, args.python, Path(args.work))
        print(json.dumps(report, sort_keys=True))
        return exit_status(report)
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__ if isinstance(exc, OSError) else str(exc)}), file=sys.stderr); return 1

if __name__ == '__main__':
    sys.exit(main())
