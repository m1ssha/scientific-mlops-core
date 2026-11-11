# Wine comparison evidence, 2026-11-11

The completed series is [post-amendment-v3-02](post-amendment-v3-02/results.json),
executed with harness 70aedce6844b4723d7d07edd00ab54034fea381a and protocol v3
SHA-256 508e3891196cdedb347916235d24944fa80085e7ee48d7e4180c14966f37e82f.
This is an explicitly post-results operational amendment, not fulfillment of
preregistered v2. See [the method](../../../docs/evaluation-plan.md).

All eight functional criteria were observed: two S0 historical recoveries each
executed Wine once with fresh outputs and a passing raw Fraction oracle; the
six negative cases stopped before training and registered failed attempts.
Original history remained unchanged in each case. Five auxiliary network results
are unchecked; three are observed_no_forbidden_events. Neither is a network
isolation guarantee. An unchecked result is never an offline pass.

DVC pull returned zero for S2 alternative. The common SHA-256 checker detected
corruption and prevented training; this is not native DVC corruption detection.
S1/S3 alternative native pull failed, while the common checker classified missing
materials; the exact native failure cause is not classified.

[sha256.json](post-amendment-v3-02/sha256.json) hashes results.json and all twenty
byte-identical raw files: source/repeat result, predictions, metrics, model and
environment for both S0 systems. UUIDs and times differ as expected; raw bytes
were not normalized. Native diagnostic records are schema-projected; full raw
command and process logs remain private, with their hashes in results.json.
This is operator-recorded provenance and independent technical review, not a
cryptographic proof of execution or a second GitHub account approval.

Earlier attempts remain separate and unchanged:

- [primary-01 and primary-02](../../../docs/evaluation/evidence-instrumentation/attempts-v2.json):
  two and three started cases under v2; each series stopped, original blocked
  audits retained without retroactive attribution.
- [first post-amendment-v3](stopped-v3.json): six started cases; stopped at S2
  alternative because the fault injector could not write a mode-0444 DVC object.
  Native pull and an MLflow attempt were not reached in that case. S3 was not
  started. Its five completed cases are not stitched into the final series.

The completed series uses a fresh workspace, caches, venv and distinct attempt
IDs after the reviewed injector fix. No speed, storage, portability, statistical
significance or novelty result is established. The source reference is a
caller-reported import, not a new source training run. Reproduction instructions
and tool locks are in [the harness README](../README.md) and the method.
