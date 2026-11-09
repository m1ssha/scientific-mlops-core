package catalog

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"reflect"
	"sort"

	"github.com/m1ssha/scientific-mlops-core/object"
)

type Service struct {
	metadata MetadataStore
	objects  object.Store
}

func NewService(metadata MetadataStore, objects object.Store) *Service {
	return &Service{metadata, objects}
}

// InitRepository explicitly initializes a local repository when the adapter supports it.
func (s *Service) InitRepository(ctx context.Context, id ID) error {
	initializer, ok := s.metadata.(interface {
		InitRepository(context.Context, ID) error
	})
	if !ok {
		return ErrStorage
	}
	return initializer.InitRepository(ctx, id)
}
func (s *Service) Repository(ctx context.Context) (View, error) { return s.metadata.Read(ctx) }
func (s *Service) GetOperation(ctx context.Context, id ID) (Receipt, error) {
	if !validID(id) {
		return Receipt{}, ErrInvalidInput
	}
	v, e := s.metadata.Read(ctx)
	if e != nil {
		return Receipt{}, e
	}
	o, ok := v.GetOperation(id)
	if !ok {
		return Receipt{}, ErrNotFound
	}
	return o.Result, nil
}
func (s *Service) GetReference(ctx context.Context, name string) (ReferenceResult, error) {
	if !validName(name) {
		return ReferenceResult{}, ErrInvalidInput
	}
	v, e := s.metadata.Read(ctx)
	if e != nil {
		return ReferenceResult{}, e
	}
	r, ok := v.GetReference(name)
	if !ok {
		return ReferenceResult{}, ErrNotFound
	}
	return ReferenceResult{name, r.TargetKind, r.TargetID, r.Revision}, nil
}
func (s *Service) GetDataset(ctx context.Context, id ID) (Record, error) {
	if !validID(id) {
		return Record{}, ErrInvalidInput
	}
	v, e := s.metadata.Read(ctx)
	if e != nil {
		return Record{}, e
	}
	r, ok := v.GetRecord(id)
	if !ok || r.Kind != Dataset {
		return Record{}, ErrNotFound
	}
	return r, nil
}
func (s *Service) ListDatasets(ctx context.Context, o ListOptions) (Page, error) {
	if o.AfterSequence < 0 || o.Limit <= 0 || o.ViewSequence < 0 || o.AfterSequence > o.ViewSequence {
		return Page{}, ErrInvalidInput
	}
	v, e := s.metadata.Read(ctx)
	if e != nil {
		return Page{}, e
	}
	if o.ViewSequence > v.Sequence() {
		return Page{}, ErrInvalidInput
	}
	items := []Record{}
	for _, r := range v.ListRecords() {
		if r.Kind == Dataset && r.Sequence > o.AfterSequence && r.Sequence <= o.ViewSequence {
			items = append(items, r)
		}
	}
	sort.Slice(items, func(i, j int) bool { return items[i].Sequence < items[j].Sequence })
	p := Page{SchemaVersion: 1, ViewSequence: o.ViewSequence, Items: items}
	if int64(len(items)) > o.Limit {
		p.Items = items[:o.Limit]
		n := p.Items[len(p.Items)-1].Sequence
		p.NextAfterSequence = &n
	}
	return p, nil
}
func normalize(c Command, typ string) (Command, error) {
	if c.SchemaVersion != 1 {
		return c, ErrUnsupportedSchema
	}
	if !validID(c.RepositoryID) || !validID(c.OperationID) || c.Type != typ {
		return c, ErrInvalidInput
	}
	switch typ {
	case "publish_dataset":
		b, ok := c.Body.(DatasetBody)
		if !ok {
			return c, ErrInvalidInput
		}
		var err error
		b, err = normalizeDataset(b)
		if err != nil {
			return c, err
		}
		c.Body = b
		if c.RefUpdate != nil {
			u := *c.RefUpdate
			if !validName(u.Name) || u.ExpectedRevision < 0 {
				return c, ErrInvalidInput
			}
			c.RefUpdate = &u
		}
	case "set_reference":
		b, ok := c.Body.(SetReferenceBody)
		if !ok || !validName(b.Name) || !validID(b.TargetID) || b.TargetKind != Dataset || b.ExpectedRevision < 0 || c.RefUpdate != nil {
			return c, ErrInvalidInput
		}
		c.Body = b
	default:
		return c, ErrInvalidInput
	}
	return c, nil
}
func randomID(v View) (ID, error) {
	for {
		var b [16]byte
		if _, e := rand.Read(b[:]); e != nil {
			return "", fmt.Errorf("%w: %v", ErrStorage, e)
		}
		id := ID(hex.EncodeToString(b[:]))
		if _, ok := v.GetRecord(id); !ok {
			return id, nil
		}
	}
}
func sameCommand(a, b Command) bool {
	aa, _ := json.Marshal(a)
	bb, _ := json.Marshal(b)
	return reflect.DeepEqual(aa, bb)
}
func verify(ctx context.Context, s object.Store, ref object.Ref) error {
	if s == nil {
		return ErrStorage
	}
	e := s.Verify(ctx, ref)
	switch {
	case errors.Is(e, object.ErrNotFound):
		return fmt.Errorf("%w: %w", ErrMissingDependency, e)
	case errors.Is(e, object.ErrIntegrity):
		return fmt.Errorf("%w: %w", ErrIntegrity, e)
	case errors.Is(e, object.ErrStorage):
		return fmt.Errorf("%w: %w", ErrStorage, e)
	default:
		return e
	}
}
func checkRef(v View, name string, expected int64, kind RecordKind) (int64, error) {
	r, ok := v.GetReference(name)
	if !ok {
		if expected != 0 {
			return 0, ErrConflict
		}
		return 1, nil
	}
	if expected != r.Revision || r.TargetKind != kind || r.Revision == math.MaxInt64 {
		return 0, ErrConflict
	}
	return r.Revision + 1, nil
}
func (s *Service) PublishDataset(ctx context.Context, c Command) (Receipt, error) {
	return s.mutate(ctx, c, "publish_dataset")
}
func (s *Service) SetReference(ctx context.Context, c Command) (Receipt, error) {
	return s.mutate(ctx, c, "set_reference")
}
func (s *Service) mutate(ctx context.Context, c Command, typ string) (result Receipt, err error) {
	c, err = normalize(c, typ)
	if err != nil {
		return
	}
	err = s.metadata.Update(ctx, func(tx Tx) error {
		v := tx.View()
		if v.RepositoryID() != c.RepositoryID {
			return ErrInvalidInput
		}
		if prior, ok := v.GetOperation(c.OperationID); ok {
			if !sameCommand(prior.Command, c) {
				return ErrOperationConflict
			}
			result = prior.Result
			return nil
		}
		if v.Sequence() == math.MaxInt64 {
			return ErrConflict
		}
		result = Receipt{SchemaVersion: 1, OperationID: c.OperationID, Sequence: v.Sequence() + 1}
		var target ID
		var kind RecordKind
		var name string
		var expected int64
		var update bool
		if typ == "publish_dataset" {
			b := c.Body.(DatasetBody)
			if b.PredecessorID != nil {
				p, ok := v.GetRecord(*b.PredecessorID)
				if !ok || p.Kind != Dataset {
					return ErrMissingDependency
				}
			}
			if c.RefUpdate != nil {
				name = c.RefUpdate.Name
				expected = c.RefUpdate.ExpectedRevision
				kind = Dataset
				update = true
				if _, e := checkRef(v, name, expected, kind); e != nil {
					return e
				}
			}
			for _, f := range b.Files {
				if e := verify(ctx, s.objects, f.Object); e != nil {
					return e
				}
			}
			id, e := randomID(v)
			if e != nil {
				return e
			}
			target = id
			kind = Dataset
			record := Record{1, id, kind, result.Sequence, b}
			if e = tx.AddRecord(record); e != nil {
				return e
			}
			result.RecordID = &id
			result.RecordKind = &kind
		} else {
			b := c.Body.(SetReferenceBody)
			target = b.TargetID
			kind = b.TargetKind
			name = b.Name
			expected = b.ExpectedRevision
			update = true
			r, ok := v.GetRecord(target)
			if !ok || r.Kind != kind {
				return ErrMissingDependency
			}
		}
		if update {
			rev, e := checkRef(v, name, expected, kind)
			if e != nil {
				return e
			}
			ref := Reference{kind, target, rev}
			if e = tx.CompareAndSwapReference(name, expected, ref); e != nil {
				return e
			}
			result.Reference = &ReferenceResult{name, kind, target, rev}
		}
		return tx.AddOperation(c.OperationID, Operation{c, result})
	})
	return
}
