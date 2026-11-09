package catalog

import (
	"fmt"
	"sort"
	"sync"
)

type generation struct {
	Repository ID                   `json:"repository_id"`
	Seq        int64                `json:"sequence"`
	Records    map[ID]Record        `json:"records"`
	References map[string]Reference `json:"references"`
	Operations map[ID]Operation     `json:"operations"`
}
type snapshot struct{ g generation }

func copyRecord(r Record) Record {
	r.Body.Files = append([]DatasetFile{}, r.Body.Files...)
	if r.Body.PredecessorID != nil {
		id := *r.Body.PredecessorID
		r.Body.PredecessorID = &id
	}
	return r
}
func copyCommand(c Command) Command {
	if b, ok := c.Body.(DatasetBody); ok {
		b, _ = normalizeDataset(b)
		c.Body = b
	}
	if c.RefUpdate != nil {
		u := *c.RefUpdate
		c.RefUpdate = &u
	}
	return c
}
func copyReceipt(r Receipt) Receipt {
	if r.RecordID != nil {
		id := *r.RecordID
		r.RecordID = &id
	}
	if r.RecordKind != nil {
		k := *r.RecordKind
		r.RecordKind = &k
	}
	if r.Reference != nil {
		v := *r.Reference
		r.Reference = &v
	}
	return r
}
func copyOperation(o Operation) Operation {
	o.Command = copyCommand(o.Command)
	o.Result = copyReceipt(o.Result)
	return o
}
func (v snapshot) RepositoryID() ID               { return v.g.Repository }
func (v snapshot) Sequence() int64                { return v.g.Seq }
func (v snapshot) GetRecord(id ID) (Record, bool) { r, ok := v.g.Records[id]; return copyRecord(r), ok }
func (v snapshot) GetReference(name string) (Reference, bool) {
	r, ok := v.g.References[name]
	return r, ok
}
func (v snapshot) GetOperation(id ID) (Operation, bool) {
	r, ok := v.g.Operations[id]
	return copyOperation(r), ok
}
func (v snapshot) ListRecords() []Record {
	r := make([]Record, 0, len(v.g.Records))
	for _, x := range v.g.Records {
		r = append(r, copyRecord(x))
	}
	sort.Slice(r, func(i, j int) bool { return r[i].Sequence < r[j].Sequence })
	return r
}
func cloneGeneration(g generation) generation {
	n := generation{g.Repository, g.Seq, map[ID]Record{}, map[string]Reference{}, map[ID]Operation{}}
	for k, v := range g.Records {
		n.Records[k] = copyRecord(v)
	}
	for k, v := range g.References {
		n.References[k] = v
	}
	for k, v := range g.Operations {
		n.Operations[k] = copyOperation(v)
	}
	return n
}

type transaction struct {
	mu      sync.Mutex
	g       generation
	changed bool
	closed  bool
}

// finish invalidates the transaction and hands commit a detached snapshot.
func (t *transaction) finish() (generation, bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.closed = true
	return cloneGeneration(t.g), t.changed
}
func (t *transaction) checkOpen() error {
	if t.closed {
		return fmt.Errorf("%w: transaction closed", ErrInvalidInput)
	}
	return nil
}

// View returns nil after the callback has ended.
func (t *transaction) View() View {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.closed {
		return nil
	}
	return snapshot{cloneGeneration(t.g)}
}
func (t *transaction) AddRecord(r Record) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if e := t.checkOpen(); e != nil {
		return e
	}
	if _, ok := t.g.Records[r.ID]; ok {
		return ErrConflict
	}
	t.g.Records[r.ID] = copyRecord(r)
	t.changed = true
	return nil
}
func (t *transaction) AddOperation(id ID, o Operation) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if e := t.checkOpen(); e != nil {
		return e
	}
	if _, ok := t.g.Operations[id]; ok {
		return ErrOperationConflict
	}
	t.g.Operations[id] = copyOperation(o)
	t.g.Seq++
	t.changed = true
	return nil
}
func (t *transaction) CompareAndSwapReference(name string, expected int64, r Reference) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if e := t.checkOpen(); e != nil {
		return e
	}
	revision, e := checkRef(snapshot{t.g}, name, expected, r.TargetKind)
	if e != nil {
		return e
	}
	if r.Revision != revision {
		return ErrInvalidInput
	}
	t.g.References[name] = r
	t.changed = true
	return nil
}
