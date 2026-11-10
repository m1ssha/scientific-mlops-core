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

The first slice freezes these exported signatures in package object:

```go
type Ref struct {
    Algorithm string `json:"algorithm"`
    Digest    string `json:"digest"`
    Size      int64  `json:"size"`
}
func (ref Ref) Validate() error
type Store interface {
    Put(context.Context, Ref, io.Reader) (Ref, error)
    Verify(context.Context, Ref) error
    Open(context.Context, Ref) (io.ReadCloser, error)
}
```

Package storage/local exports New(root string) (*Store, error). It initializes
an absent root and object/staging directories, rejects an invalid/unsafe existing
layout, and never repairs or deletes existing objects. Each store root is one
research repository. Empty root strings are invalid input. Constructor failures
remain errors, not an empty usable store. Methods are safe for concurrent calls.
Package object exports ErrInvalidRef, ErrNotFound, ErrIntegrity, ErrStorage and
ErrUncertain for errors.Is; wrapped context errors preserve their identity.

The object.Store interface has these semantics:

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

The exact success shape is {"object":{"algorithm":"sha256","digest":"...",
"size":0}}; get additionally includes an "output" string. The ellipsis above
is explanatory, not a valid digest. Errors have shape
{"error":{"code":"integrity","message":"..."}} with stable codes:
invalid_input, not_found, integrity, storage, uncertain, cancelled, deadline,
and conflict (an existing get destination). Message text is diagnostic, not an
API to parse. CLI unknown commands/flags and missing/invalid arguments use
invalid_input and exit 2. No success bytes may precede verification failure.

Required checks: empty/binary/multi-buffer stream; wrong size/hash; invalid
reference; interrupted reader; cancellation; missing/corrupt object; existing
corrupt object not replaced; concurrent same-content uploads (including separate
processes); visibility before commit; reopening the store; symlink/non-regular
object paths; CLI success/error JSON and nonzero failures; no-clobber get.
Run go test, race detector, go vet and build CLI. CI must run the same checks.
No power-loss safety claim follows from these tests.

## Контракты следующего среза

Точная схема каталога, команд, снимков/запусков, API/CLI, ошибок и плана
восстановления вынесена в [контракты версии 1](contracts-v1.md), включая примеры.
ADR остаются предложением; реализованные возможности каталога описаны в README
и контрактах версии 1. Для объектов действует раздел First slice этой страницы.

План восстановления доступен через `smlops recovery plan --store DIR --run ID`.
Команда читает каталог и проверяет байты без создания каталогов хранилища;
внешние источники остаются непроверенными. Исполнение в план не входит.
