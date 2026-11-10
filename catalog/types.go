// Package catalog defines the dataset metadata contract.
package catalog

import (
	"context"
	"errors"
	"github.com/m1ssha/scientific-mlops-core/object"
)

var (
	ErrConditionMismatch = errors.New("condition mismatch")
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
	SchemaVersion int        `json:"schema_version"`
	ID            ID         `json:"id"`
	Kind          RecordKind `json:"kind"`
	Sequence      int64      `json:"sequence"`
	Body          any        `json:"body"`
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

// Tx is valid only during the Update callback. View returns nil after it closes;
// mutation methods return ErrInvalidInput.
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

const (
	Snapshot RecordKind = "snapshot"
	Run      RecordKind = "run"
)

// TypedValue has one of the tags integer, number, string, boolean or null.
// Value must be respectively int64, float64, string, bool or nil.
type TypedValue struct {
	Type  string `json:"type"`
	Value any    `json:"-"`
}
type SnapshotInput struct {
	Role         string `json:"role"`
	DatasetID    ID     `json:"dataset_id"`
	TargetPrefix string `json:"target_prefix"`
}
type Code struct {
	Repository string `json:"repository"`
	Commit     string `json:"commit"`
	EntryPoint string `json:"entry_point"`
}
type MaterialSource struct {
	Kind           string      `json:"kind"`
	Object         *object.Ref `json:"object,omitempty"`
	Locator        string      `json:"locator,omitempty"`
	ExpectedObject *object.Ref `json:"-"`
}
type Material struct {
	Name     string         `json:"name"`
	Purpose  string         `json:"purpose"`
	Required bool           `json:"required"`
	Path     string         `json:"path"`
	Source   MaterialSource `json:"source"`
}
type Split struct {
	Description string   `json:"description"`
	Materials   []string `json:"materials"`
}
type Environment struct {
	Description string                `json:"description"`
	Materials   []string              `json:"materials"`
	Conditions  map[string]TypedValue `json:"conditions"`
}
type SnapshotBody struct {
	PredecessorID *ID                   `json:"predecessor_id"`
	ProfileID     string                `json:"profile_id"`
	Inputs        []SnapshotInput       `json:"inputs"`
	Code          Code                  `json:"code"`
	Parameters    map[string]TypedValue `json:"parameters"`
	Split         Split                 `json:"split"`
	Environment   Environment           `json:"environment"`
	Materials     []Material            `json:"materials"`
}
type Observation struct {
	Status   string      `json:"status"`
	Value    *TypedValue `json:"value"`
	Evidence *string     `json:"evidence"`
}
type Metric struct {
	Definition string  `json:"definition"`
	Unit       string  `json:"unit"`
	Value      float64 `json:"value"`
}
type Output struct {
	Path      string     `json:"path"`
	Role      string     `json:"role"`
	MediaType string     `json:"media_type"`
	Object    object.Ref `json:"object"`
}
type RunBody struct {
	SnapshotID     ID                     `json:"snapshot_id"`
	SourceRunID    *ID                    `json:"source_run_id"`
	StartedAt      *string                `json:"started_at"`
	FinishedAt     *string                `json:"finished_at"`
	Outcome        string                 `json:"outcome"`
	Reason         *string                `json:"reason"`
	Observations   map[string]Observation `json:"observations"`
	Metrics        map[string]Metric      `json:"metrics"`
	Outputs        []Output               `json:"outputs"`
	EvidenceSource string                 `json:"evidence_source"`
}
