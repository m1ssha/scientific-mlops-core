# Core MVP specification

Draft revision 1, 2026-11-09. Tracks #1 and #2. This specification separates
requested behavior (R), provisional implementation choices (D), and unresolved
decisions (Q). Existing [ADRs](adr/README.md) remain proposed. Implementation of a
prototype does not accept an ADR. Only explicit maintainer agreement after review
changes an ADR to accepted.

## Scenarios and acceptance

| Scenario | Required observable result |
| --- | --- |
| Publish a dataset (R1) | Immutable manifest identifies relative paths, exact object digests and sizes; failed upload cannot publish a record. |
| Save conditions (R2) | Immutable snapshot references exact datasets, code, entry point, typed parameters, split and environment; subsequent edits create another snapshot. |
| Record execution (R3) | Register a completed success or failure supplied by an external caller. Two executions of one snapshot produce different run IDs. Library never executes ML code. |
| Inspect history (R4) | Read exact records and predecessor/provenance links. A new current reference never rewrites old records. |
| Compare (R5) | Dataset paths, snapshot conditions and run results are compared separately. Missing records are errors; missing metrics are not zero. |
| Prepare recovery (R6) | From an exact historical run, resolve its original snapshot and required materials, report checks and ordered actions with explicit blockers. No substitution with latest data. |
| Recover and repeat (R7) | CLI or an external script materializes verified materials in a clean directory, validates declared runtime conditions, executes one trusted pipeline, and registers a new run referencing the original snapshot. |
| Report evaluation (R8) | Stored-material checks, restored conditions, execution outcome and numerical comparison are distinct results. Numerical rules are fixed before main experiments. |

The current planning horizon targets foundations and early slices, not delivery
of every scenario in two weeks. Public implementation status must distinguish
planned operations from available commands.

## Entities, relationships and invariants

- Research repository: one local store denotes one project; no authentication or
  multi-project API in the first adapter (D1). Platform supplies access control.
- Object: bytes addressed by algorithm and full digest, with checked size.
  Path and storage location do not define content identity (R9).
- Dataset version: opaque ID, schema version, ordered manifest entries and
  optional exact predecessor. Logical history is separate from deduplication.
- Snapshot: opaque ID, exact dataset IDs with input roles, code reference and
  optional preserved code objects, entry point, parameters, split material or
  recipe, environment description/materials and optional predecessor.
- Run: opaque ID, exact snapshot ID, observed start/end when known, terminal
  outcome, observed-condition evidence, metrics and output artifact references.
  Optional source-run ID denotes a repeat and must resolve to the same snapshot.
- Artifact: role, media type, object reference, producing run; same bytes can be
  shared without merging run provenance.
- Current reference: mutable target and separate revision; historical records
  never depend only on a current reference.

Published records and objects cannot be replaced through the API (R10). All
logical links stay within the research repository (R11). New data or conditions
create records, while repeated execution creates a run even with identical
results (R12). A retry of one registration operation returns its saved result,
not another run (R13). Distinct attempts require distinct operation IDs.

Failure attempts may omit unavailable metrics and outputs. Unknown timestamps
are not invented. The proposed interrupted-attempt outcome/evidence rule is now specified in
[version 1 contracts](contracts-v1.md); no live-job state in core.

## Library and CLI operations

The CLI is a thin client of the library. Names below are target contracts, not a
claim that these operations exist. Exact signatures are frozen per reviewed slice.

| Library operation | Planned CLI family | Result |
| --- | --- | --- |
| PutObject / VerifyObject / OpenObject | object put / verify / get | Verified object reference, integrity result or verified stream |
| PublishDataset | dataset publish | Record ID and optional current-reference revision |
| SaveSnapshot | snapshot create | Snapshot ID |
| RegisterRun | run register | New run ID or saved retry result |
| ReadRecord / ListHistory | dataset, snapshot, run show / list | Exact immutable records, deterministic pagination/order |
| CompareDatasets / CompareSnapshots / CompareRuns | diff dataset / snapshot / run | Directional structural report |
| PlanRecovery | recovery plan | Versioned plan including blockers and check coverage |
| External materializer | recovery materialize or example script | Verified files plus explicit runtime-validation report |

Mutating metadata commands take an operation ID and explicit expected revision
when updating a named reference. They return structured errors; the CLI emits
JSON success to stdout and JSON diagnostics to stderr with nonzero exit status.
Argument/validation errors, missing records/objects, corruption, reference
conflict, reused operation ID, unsupported schema, storage I/O and uncertain
commit outcome must remain distinguishable. A storage permission error is not
reported as absence. Read-only checks never repair or rewrite history.

## Comparison

Compare saved descriptions, not current availability. Left is baseline; right is
candidate. Paths added/removed/changed use digest and size; a rename is removal
and addition. Snapshot comparison reports each input role, code, entry point,
typed parameter, split and environment. Absent, null and empty are distinct.
Run comparison includes IDs, snapshots, outcomes, metrics and output roles.

Finite numeric metrics with the same definition and units may report right minus
left. Missing/incompatible values produce an explicit non-comparable result.
No automatic ranking or tolerance judgment in structural diff. Failure to read
either record must not look like equality. Symmetry and history preservation
are acceptance properties.

## Recovery-plan meaning

A plan has schema version, check time/coverage, source run and snapshot IDs,
dataset IDs, entry point/parameters/split/environment, materials with roles,
digests/sizes/locators, check results, blockers and ordered dependent actions.
Materials distinguish required repeat inputs from optional original outputs
needed for comparison. Missing optional original output does not block training.

Each material check distinguishes verified, missing, corrupt, unchecked and I/O
failure. External code/environment/package references remain unchecked unless
their exact required bytes have been obtained and verified. A Git commit string
or lockfile alone does not establish availability of all dependencies.

Stored-material readiness requires all declared mandatory materials to pass full
byte checks. Actual runtime readiness remains unknown until the external client
checks the required conditions. Structural restoration succeeds only when both
material checks and required restored conditions pass. The plan itself never
claims execution success or numerical agreement (R14).

Actions describe obtain -> verify -> materialize -> prepare environment ->
validate conditions -> optional trusted execution -> register another run.
Core only plans/checks. A plan reserves nothing; the materializer rechecks at
use time. Different actual conditions require recorded deviations and, when
conditions change, a new snapshot. No false attachment to unchanged conditions.

## Constraints and guarantees

One trusted local user/project and one trusted educational pipeline. Begin with
local adapters; no network service, HPC, MCP, universal runner, arbitrary code,
RBAC, distributed cache or fault-tolerant infrastructure. No automatic garbage
collection. Single-host local Linux filesystem prototype (D2); power-loss and
network-filesystem guarantees require separate evidence and are not claimed.

The store is controlled by its owner. Checks detect external corruption; they
do not provide authentication, protection against a malicious owner, proof of
execution, or mathematical proof of content equality. No guarantee of runtime
portability or numerical reproducibility follows from checksums or a fixed seed.

## Open questions and decision gates

- Q1: Terminal interrupted-run evidence is proposed in version 1 contracts;
  verify it in the run implementation slice.
- Q2: Concrete pipeline/data/model, mandatory materials and metric definitions.
  Selection and pilots precede a frozen evaluation protocol; no invented tolerance.
- Q3: Typed parameter, metric and observed-environment schemas are proposed in
  version 1 contracts; verify against the selected pipeline before the run slice.
- Q4: Local metadata corruption detection and uncertain-result handling are
  specified in version 1 contracts; failure tests and explicit acceptance remain
  required before advertising implemented publication guarantees.
- Q5: Whether an autonomous export bundle is needed; first recovery may use an
  available local store, and cannot claim offline environment installation.

Reversible design choices are in [local contracts](local-contracts.md).
Significant changes to identity semantics, atomicity, supported storage profile,
or recovery guarantees require an ADR diff, independent review, explicit
maintainer agreement, and a dated decision record. Until then ADRs stay proposed
and affected work is described as a prototype. Independent slices may proceed.
