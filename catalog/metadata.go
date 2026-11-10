package catalog

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"syscall"
	"time"
)

type LocalMetadata struct {
	root     string
	testHook func(string) error
}

func NewLocalMetadata(root string) (*LocalMetadata, error) {
	if root == "" {
		return nil, ErrInvalidInput
	}
	abs, e := filepath.Abs(root)
	if e != nil {
		return nil, fmt.Errorf("%w: %v", ErrStorage, e)
	}
	p := string(filepath.Separator)
	for _, part := range splitPath(abs) {
		p = filepath.Join(p, part)
		info, e := os.Lstat(p)
		if e != nil {
			return nil, fmt.Errorf("%w: %v", ErrStorage, e)
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return nil, fmt.Errorf("%w: unsafe root", ErrStorage)
		}
	}
	return &LocalMetadata{root: abs}, nil
}
func splitPath(p string) []string {
	var parts []string
	for p != string(filepath.Separator) && p != "." {
		parts = append([]string{filepath.Base(p)}, parts...)
		p = filepath.Dir(p)
	}
	return parts
}
func syncDir(p string) error {
	f, e := os.Open(p)
	if e != nil {
		return e
	}
	e = f.Sync()
	c := f.Close()
	if e == nil {
		e = c
	}
	return e
}
func (s *LocalMetadata) InitRepository(ctx context.Context, id ID) error {
	if !validID(id) {
		return ErrInvalidInput
	}
	if e := ctx.Err(); e != nil {
		return e
	}
	dir := filepath.Join(s.root, "metadata")
	if e := os.Mkdir(dir, 0700); e != nil {
		if errors.Is(e, os.ErrExist) {
			return ErrConflict
		}
		return fmt.Errorf("%w: %v", ErrStorage, e)
	}
	lock, e := os.OpenFile(filepath.Join(dir, "lock"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return fmt.Errorf("%w: %v", ErrStorage, e)
	}
	e = lock.Sync()
	if x := lock.Close(); e == nil {
		e = x
	}
	if e != nil {
		return fmt.Errorf("%w: %v", ErrStorage, e)
	}
	g := generation{id, 0, map[ID]Record{}, map[string]Reference{}, map[ID]Operation{}}
	if e = s.commit(ctx, g); e != nil {
		return e
	}
	if e = syncDir(s.root); e != nil {
		return fmt.Errorf("%w: %v", ErrUncertain, e)
	}
	return nil
}
func (s *LocalMetadata) paths() (string, string, error) {
	dir := filepath.Join(s.root, "metadata")
	info, e := os.Lstat(dir)
	if errors.Is(e, os.ErrNotExist) {
		return "", "", ErrNotInitialized
	}
	if e != nil {
		return "", "", fmt.Errorf("%w: %v", ErrStorage, e)
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", "", ErrIntegrity
	}
	lock := filepath.Join(dir, "lock")
	file := filepath.Join(dir, "catalog.json")
	for _, p := range []string{lock, file} {
		info, e = os.Lstat(p)
		if errors.Is(e, os.ErrNotExist) {
			return "", "", fmt.Errorf("%w: incomplete_or_missing_catalog", ErrIntegrity)
		}
		if e != nil {
			return "", "", fmt.Errorf("%w: %v", ErrStorage, e)
		}
		if !info.Mode().IsRegular() {
			return "", "", ErrIntegrity
		}
	}
	return lock, file, nil
}
func (s *LocalMetadata) Read(ctx context.Context) (View, error) {
	if e := ctx.Err(); e != nil {
		return nil, e
	}
	_, p, e := s.paths()
	if e != nil {
		return nil, e
	}
	b, e := os.ReadFile(p)
	if e != nil {
		return nil, fmt.Errorf("%w: %v", ErrStorage, e)
	}
	g, e := decodeGeneration(b)
	if e != nil {
		return nil, e
	}
	return snapshot{g}, nil
}
func (s *LocalMetadata) Update(ctx context.Context, fn func(Tx) error) error {
	if fn == nil {
		return ErrInvalidInput
	}
	lock, _, e := s.paths()
	if e != nil {
		return e
	}
	f, e := os.OpenFile(lock, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if e != nil {
		return fmt.Errorf("%w: %v", ErrStorage, e)
	}
	defer f.Close()
	for {
		e = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if e == nil {
			break
		}
		if e != syscall.EWOULDBLOCK && e != syscall.EAGAIN {
			return fmt.Errorf("%w: %v", ErrStorage, e)
		}
		timer := time.NewTimer(10 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
	defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	v, e := s.Read(ctx)
	if e != nil {
		return e
	}
	t := &transaction{g: cloneGeneration(v.(snapshot).g)}
	var committed generation
	var changed bool
	// The defer runs on success, error and panic, before the transaction escapes.
	func() { defer func() { committed, changed = t.finish() }(); e = fn(t) }()
	if e != nil {
		return e
	}
	if !changed {
		return nil
	}
	if committed.Seq != v.Sequence()+1 || len(committed.Records) > len(v.ListRecords())+1 {
		return ErrIntegrity
	}
	if e = validateGeneration(committed); e != nil {
		return e
	}
	return s.commit(ctx, committed)
}
func (s *LocalMetadata) commit(ctx context.Context, g generation) error {
	payload, e := json.Marshal(g)
	if e != nil {
		return fmt.Errorf("%w: %v", ErrStorage, e)
	}
	digest := sha256.Sum256(payload)
	env := struct {
		SchemaVersion int             `json:"schema_version"`
		PayloadSHA256 string          `json:"payload_sha256"`
		Payload       json.RawMessage `json:"payload"`
	}{1, hex.EncodeToString(digest[:]), payload}
	b, e := json.Marshal(env)
	if e != nil {
		return fmt.Errorf("%w: %v", ErrStorage, e)
	}
	dir := filepath.Join(s.root, "metadata")
	f, e := os.CreateTemp(dir, ".catalog-")
	if e != nil {
		return fmt.Errorf("%w: %v", ErrStorage, e)
	}
	defer os.Remove(f.Name())
	if _, e = f.Write(b); e == nil {
		e = f.Sync()
	}
	if x := f.Close(); e == nil {
		e = x
	}
	if e != nil {
		return fmt.Errorf("%w: %v", ErrStorage, e)
	}
	if e = ctx.Err(); e != nil {
		return e
	}
	if s.testHook != nil {
		if e = s.testHook("before_rename"); e != nil {
			return fmt.Errorf("%w: %v", ErrStorage, e)
		}
	}
	if e = os.Rename(f.Name(), filepath.Join(dir, "catalog.json")); e != nil {
		return fmt.Errorf("%w: %v", ErrStorage, e)
	}
	if s.testHook != nil {
		if e = s.testHook("after_rename"); e != nil {
			return fmt.Errorf("%w: %v", ErrUncertain, e)
		}
	}
	if e = syncDir(dir); e != nil {
		return fmt.Errorf("%w: %v", ErrUncertain, e)
	}
	return nil
}
func decodeGeneration(b []byte) (generation, error) {
	var z generation
	var envelope struct {
		SchemaVersion int             `json:"schema_version"`
		PayloadSHA256 string          `json:"payload_sha256"`
		Payload       json.RawMessage `json:"payload"`
	}
	if e := decodeExact(b, &envelope, "schema_version", "payload_sha256", "payload"); e != nil {
		return z, fmt.Errorf("%w: %v", ErrIntegrity, e)
	}
	if envelope.SchemaVersion != 1 {
		return z, ErrUnsupportedSchema
	}
	sum := sha256.Sum256(envelope.Payload)
	if envelope.PayloadSHA256 != hex.EncodeToString(sum[:]) {
		return z, ErrIntegrity
	}
	var raw struct {
		Repository ID                         `json:"repository_id"`
		Seq        int64                      `json:"sequence"`
		Records    map[ID]json.RawMessage     `json:"records"`
		References map[string]json.RawMessage `json:"references"`
		Operations map[ID]json.RawMessage     `json:"operations"`
	}
	if e := decodeExact(envelope.Payload, &raw, "repository_id", "sequence", "records", "references", "operations"); e != nil || raw.Records == nil || raw.References == nil || raw.Operations == nil {
		return z, ErrIntegrity
	}
	g := generation{raw.Repository, raw.Seq, map[ID]Record{}, map[string]Reference{}, map[ID]Operation{}}
	for id, b := range raw.Records {
		var rr struct {
			SchemaVersion int             `json:"schema_version"`
			ID            ID              `json:"id"`
			Kind          RecordKind      `json:"kind"`
			Sequence      int64           `json:"sequence"`
			Body          json.RawMessage `json:"body"`
		}
		if e := decodeExact(b, &rr, "schema_version", "id", "kind", "sequence", "body"); e != nil {
			return z, ErrIntegrity
		}
		if rr.SchemaVersion != 1 {
			return z, ErrUnsupportedSchema
		}
		typ := map[RecordKind]string{Dataset: "publish_dataset", Snapshot: "save_snapshot", Run: "register_run"}[rr.Kind]
		if typ == "" {
			return z, ErrUnsupportedKind
		}
		command := fmt.Sprintf(`{"schema_version":1,"repository_id":"%s","operation_id":"00000000000000000000000000000000","type":"%s","body":%s,"ref_update":null}`, raw.Repository, typ, rr.Body)
		c, e := DecodeCommand([]byte(command))
		if e != nil {
			return z, ErrIntegrity
		}
		original, _ := json.Marshal(c.Body)
		var rawNormalized any
		var encodedNormalized any
		if decodeNumber(rr.Body, &rawNormalized) != nil || decodeNumber(original, &encodedNormalized) != nil || !equalJSON(rawNormalized, encodedNormalized) {
			return z, ErrIntegrity
		}
		g.Records[id] = Record{rr.SchemaVersion, rr.ID, rr.Kind, rr.Sequence, c.Body}
	}
	for name, b := range raw.References {
		var r Reference
		if e := decodeExact(b, &r, "target_kind", "target_id", "revision"); e != nil {
			return z, ErrIntegrity
		}
		g.References[name] = r
	}
	for id, b := range raw.Operations {
		var o struct {
			Command json.RawMessage `json:"command"`
			Result  json.RawMessage `json:"result"`
		}
		if e := decodeExact(b, &o, "command", "result"); e != nil {
			return z, ErrIntegrity
		}
		var kindProbe struct {
			Type string `json:"type"`
		}
		if e := decodeExact(o.Command, &struct {
			SchemaVersion json.RawMessage `json:"schema_version"`
			RepositoryID  json.RawMessage `json:"repository_id"`
			OperationID   json.RawMessage `json:"operation_id"`
			Type          json.RawMessage `json:"type"`
			Body          json.RawMessage `json:"body"`
			RefUpdate     json.RawMessage `json:"ref_update"`
		}{}, "schema_version", "repository_id", "operation_id", "type", "body", "ref_update"); e != nil {
			return z, ErrIntegrity
		}
		if e := json.Unmarshal(o.Command, &kindProbe); e != nil {
			return z, ErrIntegrity
		}
		if kindProbe.Type != "publish_dataset" && kindProbe.Type != "save_snapshot" && kindProbe.Type != "register_run" && kindProbe.Type != "set_reference" {
			return z, ErrUnsupportedKind
		}
		c, e := DecodeCommand(o.Command)
		if errors.Is(e, ErrUnsupportedSchema) {
			return z, e
		}
		if e != nil {
			return z, ErrIntegrity
		}
		var r Receipt
		if e = decodeExact(o.Result, &r, "schema_version", "operation_id", "record_kind", "record_id", "sequence", "reference"); e != nil {
			return z, ErrIntegrity
		}
		if r.SchemaVersion != 1 {
			return z, ErrUnsupportedSchema
		}
		if !bytes.Equal(bytes.TrimSpace(o.Result), []byte("null")) && r.Reference != nil {
			var fields map[string]json.RawMessage
			_ = json.Unmarshal(o.Result, &fields)
			var ref ReferenceResult
			if e = decodeExact(fields["reference"], &ref, "name", "target_kind", "target_id", "revision"); e != nil {
				return z, ErrIntegrity
			}
		}
		var original struct {
			Body json.RawMessage `json:"body"`
		}
		if json.Unmarshal(o.Command, &original) != nil {
			return z, ErrIntegrity
		}
		bodyJSON, _ := json.Marshal(c.Body)
		var rawBody, normalizedBody any
		if decodeNumber(original.Body, &rawBody) != nil || decodeNumber(bodyJSON, &normalizedBody) != nil || !equalJSON(rawBody, normalizedBody) {
			return z, ErrIntegrity
		}

		g.Operations[id] = Operation{c, r}
	}
	if e := validateGeneration(g); e != nil {
		return z, e
	}
	return g, nil
}
func validateGeneration(g generation) error {
	if !validID(g.Repository) || g.Seq < 0 || int64(len(g.Operations)) != g.Seq {
		return ErrIntegrity
	}
	ops := make([]Operation, 0, len(g.Operations))
	for id, o := range g.Operations {
		if id != o.Command.OperationID || id != o.Result.OperationID || o.Command.RepositoryID != g.Repository || o.Result.SchemaVersion != 1 {
			return ErrIntegrity
		}
		c, e := normalize(o.Command, o.Command.Type)
		if e != nil || !sameCommand(c, o.Command) {
			return ErrIntegrity
		}
		ops = append(ops, o)
	}
	sort.Slice(ops, func(i, j int) bool { return ops[i].Result.Sequence < ops[j].Result.Sequence })
	records := map[ID]Record{}
	refs := map[string]Reference{}
	for i, o := range ops {
		r := o.Result
		if r.Sequence != int64(i)+1 {
			return ErrIntegrity
		}
		var name string
		var expected int64
		var kind RecordKind
		var target ID
		var update bool
		switch o.Command.Type {
		case "publish_dataset", "save_snapshot", "register_run":
			kind = map[string]RecordKind{"publish_dataset": Dataset, "save_snapshot": Snapshot, "register_run": Run}[o.Command.Type]
			if r.RecordID == nil || r.RecordKind == nil || *r.RecordKind != kind {
				return ErrIntegrity
			}
			record, ok := g.Records[*r.RecordID]
			if !ok || !validID(record.ID) || record.ID != *r.RecordID || record.SchemaVersion != 1 || record.Kind != kind || record.Sequence != r.Sequence || !sameBody(record.Body, o.Command.Body) {
				return ErrIntegrity
			}
			if _, ok = records[record.ID]; ok {
				return ErrIntegrity
			}
			partial := snapshot{generation{Records: records}}
			switch b := record.Body.(type) {
			case DatasetBody:
				if b.PredecessorID != nil {
					p, ok := records[*b.PredecessorID]
					if !ok || p.Kind != Dataset {
						return ErrIntegrity
					}
				}
			case SnapshotBody:
				if checkSnapshotRefs(partial, b) != nil {
					return ErrIntegrity
				}
			case RunBody:
				if checkRunRefs(partial, b) != nil {
					return ErrIntegrity
				}
			default:
				return ErrIntegrity
			}
			records[record.ID] = record
			target = record.ID
			if o.Command.RefUpdate != nil {
				update = true
				name = o.Command.RefUpdate.Name
				expected = o.Command.RefUpdate.ExpectedRevision
			}

		case "set_reference":
			if r.RecordID != nil || r.RecordKind != nil {
				return ErrIntegrity
			}
			b := o.Command.Body.(SetReferenceBody)
			name = b.Name
			expected = b.ExpectedRevision
			target = b.TargetID
			kind = b.TargetKind
			update = true
			if existing, ok := records[target]; !ok || existing.Kind != kind {
				return ErrIntegrity
			}
		default:
			return ErrIntegrity
		}
		if update {
			old, exists := refs[name]
			if (!exists && expected != 0) || (exists && (old.Revision != expected || old.TargetKind != kind)) || expected == int64(^uint64(0)>>1) {
				return ErrIntegrity
			}
			expected++
			ref := Reference{kind, target, expected}
			refs[name] = ref
			if r.Reference == nil || *r.Reference != (ReferenceResult{name, kind, target, expected}) {
				return ErrIntegrity
			}
		} else if r.Reference != nil {
			return ErrIntegrity
		}
	}
	if len(records) != len(g.Records) || len(refs) != len(g.References) {
		return ErrIntegrity
	}
	for k, v := range refs {
		if g.References[k] != v {
			return ErrIntegrity
		}
	}
	return nil
}
func sameBody(a, b any) bool {
	aa, _ := json.Marshal(a)
	bb, _ := json.Marshal(b)
	return bytes.Equal(aa, bb)
}

func decodeNumber(data []byte, v any) error {
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	return d.Decode(v)
}

func equalJSON(a, b any) bool {
	switch x := a.(type) {
	case json.Number:
		y, ok := b.(json.Number)
		if !ok {
			return false
		}
		xr, xok := new(big.Rat).SetString(string(x))
		yr, yok := new(big.Rat).SetString(string(y))
		return xok && yok && xr.Cmp(yr) == 0
	case map[string]any:
		y, ok := b.(map[string]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for k, v := range x {
			w, ok := y[k]
			if !ok || !equalJSON(v, w) {
				return false
			}
		}
		return true
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !equalJSON(x[i], y[i]) {
				return false
			}
		}
		return true
	default:
		return a == b
	}
}
