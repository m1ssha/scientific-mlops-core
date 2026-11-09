// Package catalog defines the dataset metadata contract.
package catalog

import (
	"context"
	"errors"
	"github.com/m1ssha/scientific-mlops-core/object"
)

var (
	ErrInvalidInput      = errors.New("invalid input")
	ErrNotInitialized    = errors.New("repository not initialized")
	ErrNotFound          = errors.New("not found")
	ErrMissingDependency = errors.New("missing dependency")
	ErrIntegrity         = errors.New("integrity violation")
	ErrConflict          = errors.New("conflict")
	ErrOperationConflict = errors.New("operation conflict")
	ErrUnsupportedSchema = errors.New("unsupported schema")
	ErrStorage           = errors.New("storage error")
	ErrUncertain         = errors.New("uncertain commit outcome")
	ErrUnsupportedKind   = errors.New("unsupported record or command kind")
)

type ID string
type RecordKind string

const Dataset RecordKind = "dataset"

type DatasetFile struct {
	Path   string     `json:"path"`
	Object object.Ref `json:"object"`
}
type DatasetBody struct {
	PredecessorID *ID           `json:"predecessor_id"`
	Files         []DatasetFile `json:"files"`
}
type Reference struct {
	TargetKind RecordKind `json:"target_kind"`
	TargetID   ID         `json:"target_id"`
	Revision   int64      `json:"revision"`
}
type ReferenceResult struct {
	Name       string     `json:"name"`
	TargetKind RecordKind `json:"target_kind"`
	TargetID   ID         `json:"target_id"`
	Revision   int64      `json:"revision"`
}
type RefUpdate struct {
	Name             string `json:"name"`
	ExpectedRevision int64  `json:"expected_revision"`
}
type SetReferenceBody struct {
	Name             string     `json:"name"`
	TargetKind       RecordKind `json:"target_kind"`
	TargetID         ID         `json:"target_id"`
	ExpectedRevision int64      `json:"expected_revision"`
}
type Command struct {
	SchemaVersion int        `json:"schema_version"`
	RepositoryID  ID         `json:"repository_id"`
	OperationID   ID         `json:"operation_id"`
	Type          string     `json:"type"`
	Body          any        `json:"body"`
	RefUpdate     *RefUpdate `json:"ref_update"`
}
type Receipt struct {
	SchemaVersion int              `json:"schema_version"`
	OperationID   ID               `json:"operation_id"`
	RecordKind    *RecordKind      `json:"record_kind"`
	RecordID      *ID              `json:"record_id"`
	Sequence      int64            `json:"sequence"`
	Reference     *ReferenceResult `json:"reference"`
}
type Record struct {
	SchemaVersion int         `json:"schema_version"`
	ID            ID          `json:"id"`
	Kind          RecordKind  `json:"kind"`
	Sequence      int64       `json:"sequence"`
	Body          DatasetBody `json:"body"`
}
type Operation struct {
	Command Command `json:"command"`
	Result  Receipt `json:"result"`
}
type ListOptions struct {
	AfterSequence int64
	Limit         int64
	ViewSequence  int64
}
type Page struct {
	SchemaVersion     int      `json:"schema_version"`
	ViewSequence      int64    `json:"view_sequence"`
	Items             []Record `json:"items"`
	NextAfterSequence *int64   `json:"next_after_sequence"`
}

// View exposes copies of a single committed generation.
type View interface {
	RepositoryID() ID
	Sequence() int64
	GetRecord(ID) (Record, bool)
	GetReference(string) (Reference, bool)
	GetOperation(ID) (Operation, bool)
	ListRecords() []Record
}
type Tx interface {
	View() View
	AddRecord(Record) error
	AddOperation(ID, Operation) error
	CompareAndSwapReference(string, int64, Reference) error
}
type MetadataStore interface {
	Read(context.Context) (View, error)
	Update(context.Context, func(Tx) error) error
}
