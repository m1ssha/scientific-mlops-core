# Local prototype contracts

Draft revision 1, 2026-11-09. Addresses #2 and supports the first independently
testable object slice. These are provisional choices under the proposed ADRs
[0004](adr/0004-version-identity.md), [0005](adr/0005-storage.md),
[0006](adr/0006-publication-concurrency.md) and [0009](adr/0009-api-versioning.md).
They do not accept those ADRs or claim implementation before checks pass.

## First slice: verified objects

Go module: github.com/m1ssha/scientific-mlops-core. Public packages: object for
references/errors/storage interface; storage/local for the local object adapter.
CLI: cmd/smlops. Toolchain is pinned in go.mod and CI by the first code slice.
No external Go dependency is needed for this slice.

An ObjectRef contains Algorithm = sha256, Digest = exactly 64 lowercase hex
characters, Size = nonnegative int64. Size is verified metadata, not part of the
physical content address. Unsupported algorithm, uppercase/short/invalid digest
or negative size is invalid input. Zero-byte objects are valid.

The object.Store interface has these semantics (exact exported spellings are
established by the implementation and documented before merge):

| Operation | Contract |
| --- | --- |
| Put(context, expected reference, reader) | Stream bytes to staging, calculate actual size and digest, reject mismatch or read/cancellation error, publish only complete verified bytes without replacement. Return the expected reference on success. |
| Verify(context, reference) | Open a regular object file and read all bytes, checking size and digest. Existence alone is insufficient. |
| Open(context, reference) | Verify the complete object before returning a reader at offset zero. Reading afterward does not reserve against external owner mutation. Caller closes the reader. |

Validation precedes storage access. Errors support errors.Is for invalid input,
not found, integrity violation, storage I/O and uncertain publication outcome;
context cancellation/deadline errors retain their standard identity. Include the
affected object in diagnostics without credentials. Reader errors remain errors
and never result in publication. Wrong size/digest of supplied bytes is an
integrity violation. Permission and device errors remain storage I/O.

Objects live below a trusted owner-controlled store root, at a path derived only
from validated algorithm/digest. Manifest paths are never object addresses.
Temporary files live on the same filesystem and are not readable as objects.
Before publishing: finish copy, check context/size/digest, sync and close the
staged file. Publish through atomic create-if-absent, never replacing a final
object. If the address already exists, verify its complete bytes and size before
returning success. A corrupt existing object stays corrupt and produces an
integrity error; this API never silently repairs it. Concurrent identical uploads
converge on one valid object. No partially copied final object becomes visible.

Static symbolic links/non-regular files at object addresses and escaping store
subdirectories must be rejected. Concurrent hostile replacement of the trusted
root by its owner is outside the profile. If post-publication synchronization
fails, return uncertain outcome rather than promise absence; verification can
resolve presence and integrity. Cancellation after an atomic publication point
may leave a valid object; this must be documented. Failures before that point
leave no new final object. Best-effort cleanup of this operation's staging files
is allowed; abandoned staging files never count as objects. No GC or delete API.

CLI first-slice contract:

```text
smlops object put --store DIR --file PATH --sha256 DIGEST --size BYTES
smlops object verify --store DIR --sha256 DIGEST --size BYTES
smlops object get --store DIR --sha256 DIGEST --size BYTES --output NEW_PATH
```

Commands call the same library operations. Put/verify/get success emits one JSON
object to stdout with the reference (get also identifies its destination).
Errors emit one JSON diagnostic to stderr and exit nonzero; no success JSON on
error. Exit 2 is usage/invalid input, exit 1 is operational failure. Get verifies
before creating a destination, creates a new regular file without replacement,
and removes its newly created output on copy failure. It never executes code.
The output parent is a caller-selected trusted existing directory.

Required checks: empty/binary/multi-buffer stream; wrong size/hash; invalid
reference; interrupted reader; cancellation; missing/corrupt object; existing
corrupt object not replaced; concurrent same-content uploads (including separate
processes); visibility before commit; reopening the store; symlink/non-regular
object paths; CLI success/error JSON and nonzero failures; no-clobber get.
Run go test, race detector, go vet and build CLI. CI must run the same checks.
No power-loss safety claim follows from these tests.

## Metadata slice: decisions to verify before implementation

The object slice can proceed without implementing this section. Before dataset
publication, review and freeze its JSON schema and commit/failure contract.

- Opaque record and operation IDs: 32 lowercase hex characters generated from
  16 crypto/rand bytes; IDs are project-scoped and checked for conflicts. No
  manifest-content hash masquerades as a logical record ID.
- Caller supplies operation ID. Normalized typed command content is persisted
  with the result indefinitely in the MVP; retry compares complete normalized
  content, not merely a hash. Same command returns saved result before checking
  current reference revision. A reused ID with different content is conflict.
- One versioned catalog generation includes records, current-reference revisions
  and operation results. Local interprocess lock protects read/validate/commit;
  upload proceeds outside the metadata lock. One atomic generation replacement
  is the visibility point; readers see one committed generation.
- New named reference expects absence; an update supplies the observed revision.
  Revisions change on every update, including a return to an old target (ABA).
  No implicit retry with a new expected revision after conflict.
- All required local objects and record references are verified before commit.
  There is no supported deletion during publication. Owner mutation remains
  detectable external corruption, not an atomicity guarantee.
- Record, operation result and optional CAS update commit together or not at all.
  Object upload may leave orphans. A failed CAS creates no visible record/result.
  Publication without a named-reference update is allowed.
- Sync staging before replace, then sync directory. Failures after visibility
  are uncertain outcome; retry/read by operation ID resolves them. Restart never
  selects a leftover staging generation based on modification time.
- Versioned catalog envelope includes an integrity checksum of its exact payload
  bytes; it detects accidental corruption, not malicious owner rewriting.
  Unsupported versions fail explicitly. Corrupt committed catalog fails closed,
  with no automatic empty initialization, migration or history rewrite.
- Paths: nonempty relative slash-separated UTF-8 paths; reject absolute, dot,
  parent, empty components, backslash, NUL, duplicates and file/directory prefix
  collisions. Sort entries by path. Materialization must prevent symlink escape.

Tests must cover interrupted publication, first-create CAS race across processes,
same operation retry/different payload, return-to-old-target revision conflict,
readers during commit, reopening after process termination, and corrupt catalog.
Precise filesystem support and limits accompany test evidence before this
contract is advertised as implemented.

## Later snapshot, run and report schemas

JSON records and reports have explicit schema_version. Unsupported versions are
errors; old records are not silently normalized into a new meaning. Fixed-size
IDs and exact object references are validated at boundaries.

Before snapshot/run implementation, define parameter types, canonical request
normalization, metric definition/units, timestamps and interrupted outcomes.
The library validates record relationships and finite numeric values. The CLI
does not infer unknown observations. A completed run is registered once and
does not serve as a mutable job status row.

Before recovery implementation, freeze a concrete JSON fixture with checked,
missing, corrupt, unchecked and I/O results, material roles, dependent actions,
separate stored-material/runtime readiness, and a blocked external dependency.
Before numerical trials, freeze the pipeline protocol and its applicability.
No schema or tolerance is implied by starting object-storage work.
