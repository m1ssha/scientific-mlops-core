#!/usr/bin/env python3
"""Run the public local demo in a new disposable directory; never train Wine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CORE_URL = 'https://github.com/m1ssha/scientific-mlops-core.git'
PLATFORM_URL = 'https://github.com/m1ssha/scientific-mlops-platform.git'
PLATFORM_REF = '8834058357a905dc016b1a7b6101b65cb43f846c'
HISTORICAL_CORE_REF = '5c37e250896fbb67670e7a1897853f78baec144d'
SHA = re.compile(r'^[0-9a-f]{40}$')


def safe_new_work(path, forbidden):
    path = Path(os.path.abspath(path))
    if path.exists() or path.is_symlink():
        raise ValueError('work directory already exists')
    if not path.parent.is_dir():
        raise ValueError('work parent must exist')
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise ValueError('symlink in work path')
    inside = subprocess.run(['git', '-C', str(path.parent), 'rev-parse', '--is-inside-work-tree'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    if inside.returncode == 0 and inside.stdout.strip() == 'true':
        raise ValueError('work parent is inside a Git checkout')
    for other in forbidden:
        other = Path(other).resolve(strict=True)
        if path == other or path in other.parents or other in path.parents:
            raise ValueError('work directory overlaps an existing checkout')
    return path


def manifest(root):
    result = {}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError('symlink in store')
        if p.is_file():
            result[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif not p.is_dir():
            raise ValueError('nonregular store entry')
    return result


def run(args, cwd, log, env=None, expect=0):
    with log.open('ab') as out:
        out.write(('\n$ ' + ' '.join(map(str, args)) + '\n').encode())
        out.flush()
        result = subprocess.run([str(x) for x in args], cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, check=False)
    if expect == 'nonzero':
        if result.returncode == 0:
            raise RuntimeError('command unexpectedly succeeded; see local log')
    elif result.returncode != expect:
        raise RuntimeError(f'command failed ({result.returncode}); see local log')


def checkout(url, ref, dest, work, log):
    run(['git', 'clone', '--no-checkout', url, dest], work, log)
    run(['git', 'checkout', '--detach', ref], dest, log)
    actual = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=dest, check=True, capture_output=True, text=True).stdout.strip()
    if actual != ref:
        raise RuntimeError('checkout SHA does not match requested commit')
    if subprocess.run(['git', 'status', '--porcelain'], cwd=dest, check=True, capture_output=True, text=True).stdout:
        raise RuntimeError('checkout is not clean')


def verify(work, core_ref, install_browser=False):
    if not SHA.fullmatch(core_ref):
        raise ValueError('core ref must be a full lowercase commit SHA')
    work = safe_new_work(work, [Path(__file__).resolve().parents[1]])
    work.mkdir(mode=0o700)
    log = work / 'commands.log'
    core, platform = work / 'core', work / 'platform'
    checkout(CORE_URL, core_ref, core, work, log)
    checkout(PLATFORM_URL, PLATFORM_REF, platform, work, log)
    cli = work / 'smlops'
    run(['go', 'build', '-o', cli, './cmd/smlops'], core, log)
    payload = b'clean demo object\n'
    source, restored, store = work / 'source.txt', work / 'restored.txt', work / 'object-store'
    source.write_bytes(payload)
    digest, size = hashlib.sha256(payload).hexdigest(), len(payload)
    common = ['--store', store, '--sha256', digest, '--size', str(size)]
    run([cli, 'object', 'put', *common, '--file', source], work, log)
    run([cli, 'object', 'verify', *common], work, log)
    run([cli, 'object', 'get', *common, '--output', restored], work, log)
    if restored.read_bytes() != payload:
        raise RuntimeError('restored object bytes differ')
    offset = log.stat().st_size
    run([cli, 'object', 'get', *common, '--output', restored], work, log, expect='nonzero')
    with log.open('rb') as saved:
        saved.seek(offset)
        error = json.loads(saved.read().splitlines()[-1])
    if error.get('error', {}).get('code') != 'conflict':
        raise RuntimeError('repeated get did not report conflict')
    if restored.read_bytes() != payload:
        raise RuntimeError('failed get modified destination')
    web = platform / 'web'
    run(['npm', 'ci'], web, log)
    run(['npm', 'test'], web, log)
    run(['npm', 'run', 'build'], web, log)
    if install_browser:
        run(['npx', 'playwright', 'install', 'chromium'], web, log)
    demo = work / 'historical-demo'
    run([sys.executable, 'tools/wine_demo.py', '--work', demo], platform, log)
    meta = json.loads((demo / 'demo-metadata.json').read_text())
    if meta['core_commit'] != HISTORICAL_CORE_REF or meta['wheelhouse_supplied']:
        raise RuntimeError('unexpected historical core or wheelhouse')
    platform_bin = work / 'platform-bin'
    run(['go', 'build', '-o', platform_bin, './cmd/platform'], platform, log)
    before = manifest(demo / 'store')
    (work / 'store-before.json').write_text(json.dumps(before, indent=2, sort_keys=True) + '\n')
    env = os.environ.copy()
    # Avoid accidentally selecting a pre-existing or unrelated historical store.
    env.pop('WINE_HISTORICAL_STORE', None)
    env.update(WINE_WORK=str(demo), WINE_PLATFORM=str(platform_bin), UI_EVIDENCE_DIR=str(work / 'screenshots'))
    run(['node', 'scripts/wine-smoke.mjs'], web, log, env=env)
    after = manifest(demo / 'store')
    (work / 'store-after.json').write_text(json.dumps(after, indent=2, sort_keys=True) + '\n')
    if before != after:
        raise RuntimeError('historical demo source store changed')
    summary = {'core_ref': core_ref, 'platform_ref': PLATFORM_REF, 'historical_core_ref': HISTORICAL_CORE_REF,
               'object_sha256': digest, 'object_size': size, 'source_store_files': len(before),
               'result': 'passed', 'scope': 'historical import; no training or comparative evaluation'}
    (work / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True, help='new directory with an existing non-symlink parent')
    parser.add_argument('--core-ref', required=True, help='full public core commit SHA')
    parser.add_argument('--install-browser', action='store_true', help='install the Playwright-pinned Chromium')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.work, args.core_ref, args.install_browser), sort_keys=True))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('clean demo failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
