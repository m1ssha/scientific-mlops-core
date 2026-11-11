# Wine evaluation harness

This harness implements the preregistered eight-case plan in
[docs/evaluation-plan.md](../../docs/evaluation-plan.md). The harness itself is
not evaluation evidence. Do not run the cases until this exact committed harness
has independent review and the maintainer authorizes the measured series.

All work and evidence directories must be new, with existing parents, and must
not overlap the inputs or repository. The harness records the exact checked-out
commit and requires `--reviewed-commit` to match a clean HEAD descended from the
approved plan. This argument is a recorded operator assertion, not cryptographic
proof of independent review. The owner must supply the review separately.

The runner accepts `--work`, `--core-source` (full Git history),
`--platform-source`, `--wheelhouse`, `--go`, `--dvc`, `--control-python`,
`--wine-python`, `--mlflow-python`, and `--reviewed-commit`. All paths refer to
local tools/assets. Both control environments must match the full checked-in
locks, and tool versions must match protocol-v1.json. These checks run before
the series. Go is configured without telemetry or network package resolution.

Before the eight cases, the pinned core checkout is verified against Git objects
and the CLI is built once as tooling setup. Its command, output, trace and binary
SHA-256 are retained; any ambiguous build trace is recorded and is not an
offline pass. The binary SHA-256 is rechecked in every case. This build is not
recovery, training, or an input/output cache. Per-case setup, recovery and
execution are separately traced and any ambiguous or incomplete trace blocks the
series. Local binds/socket probes are distinguished from network activity.

Each case imports the same caller-reported source and creates C1 and C2 without
training. For the alternative, the core fixture generator is a common setup
helper to obtain the same inventory, source metadata and C2 bytes; its store is
then removed and is not a recovery asset or a native DVC/MLflow feature. The
alternative uses only its named Git, DVC remote and MLflow assets for recovery;
common integrity and Wine helpers remain external. The core case copies its
store and removes the disposable source store. Neither path uses an existing
Wine venv as the restored training environment.

Per-case `setup.json` lists permitted assets and material hashes. Inputs are
copied, not hardlinked, and mutation targets are checked against the protocol
before any deletion or same-size corruption. Core-native diagnostics and DVC
pull exit are retained separately from the common checker. The runner starts a
separate MLflow attempt before baseline materialization and records failed
attempts independently of a DVC experiment. The existing Wine reference UUID,
new Wine UUID, process exec trace and five raw outputs have distinct roles.
The external Fraction oracle comes only from the pinned core file after its
SHA-256 check.

The attempts index is appended and fsynced before each case. Command argv,
restricted environment, cwd, stdout, stderr, exit, timestamps, tracer PID and
raw process/network traces stay in the new private work directory. Cases follow
the fixed order; a blocked/unknown result stops the series. A stopped or killed
series is not silently retried and is not a successful evaluation. Timeouts
are guards, not performance measurements. Source and old records are checked
separately from new experiment refs and MLflow records.

After a complete eight-case series, `export.py --work DIR --destination NEW_DIR`
exports a small structured, path-free summary and byte-identical S0 outputs with
SHA-256/size manifest. Full logs stay private. Git's -text attribute preserves
bytes of exported evidence. An incomplete or blocked series cannot be exported
as complete.

Synthetic checks, which do not execute Wine, run with:

```sh
python -m unittest discover -s examples/evaluation -p 'test_*.py' -v
```

Set `EVAL_TEST_MLFLOW_PYTHON` to the locked MLflow control interpreter for the
optional synthetic tracker test. The tests do not approve the harness or run
the eight cases. Network tracing is observation, not a sandbox: inbound or
outbound Internet activity, ambiguous descriptors, truncated traces or unknown
syscalls must never be interpreted as an offline pass.
