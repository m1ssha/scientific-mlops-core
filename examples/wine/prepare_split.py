#!/usr/bin/env python3
"""Regenerate split.json from the fixed raw dataset in the locked environment."""
import hashlib
import json
from pathlib import Path
from sklearn.model_selection import train_test_split
root = Path(__file__).resolve().parent
data = (root / 'data/wine.data').read_bytes()
labels = [int(row.split(b',', 1)[0]) for row in data.splitlines()]
train, test = train_test_split(list(range(1, len(labels) + 1)), test_size=0.25,
                               random_state=42, stratify=labels)
split = {'schema_version': 1, 'dataset_sha256': hashlib.sha256(data).hexdigest(),
         'strategy': {'name': 'stratified_train_test_split', 'seed': 42, 'test_size': 0.25,
                      'implementation': 'scikit-learn 1.9.1'}, 'train': train, 'test': test}
print(json.dumps(split, indent=2))
