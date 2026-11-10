#!/usr/bin/env python3
"""Fixed validation helper for the pinned Wine v1 pipeline, run in its venv."""
import hashlib
import json
import sys
import types
from pathlib import Path

PIN = '987c1c919830ba38f7caab79ccecf0c839e1d66823cd8ffb910f2094adcff4bf'

def main():
    mode, root, expected, *rest = sys.argv[1:]
    root, expected = Path(root), Path(expected)
    data = (root / 'pipeline.py').read_bytes()
    if hashlib.sha256(data).hexdigest() != PIN:
        raise ValueError('unapproved pipeline')
    module = types.ModuleType('approved_wine')
    module.__file__ = str(root / 'pipeline.py')
    exec(compile(data, module.__file__, 'exec'), module.__dict__)
    rows, split, config = module.verify(root)
    source_env = module.read(expected)
    module.validate_environment(source_env, root)
    if mode == 'preflight':
        # Import the same numerical stack as train before inspecting loaded pools.
        import numpy
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.linear_model import LogisticRegression
        from sklearn.dummy import DummyClassifier
        actual = module.environment(root)
        if not module.strict_equal(actual, source_env):
            raise ValueError('runtime fingerprint differs from reference')
        return {'environment': actual, 'config': config, 'seed': split['strategy']['seed']}
    if mode != 'validate' or len(rest) != 2:
        raise ValueError('invalid helper mode')
    output, original = map(Path, rest)
    _, metrics, actual, _ = module.validate_output(root, output, rows, split)
    if not module.strict_equal(actual, source_env):
        raise ValueError('output fingerprint differs from reference')
    record = module.read(output / 'result.json')
    if record['phase'] != 'validation':
        raise ValueError('invalid execution phase')
    comparison = module.compare(root, original, root, output) if original.is_dir() else {'numerical': 'unchecked'}
    return {'environment': actual, 'metrics': metrics, 'record': record, 'comparison': comparison}

if __name__ == '__main__':
    try:
        print(json.dumps(main(), sort_keys=True, allow_nan=False))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        sys.exit(1)
