package catalog

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/m1ssha/scientific-mlops-core/object"
	"github.com/m1ssha/scientific-mlops-core/storage/local"
)

func TestExperimentLifecycle(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	m, _ := NewLocalMetadata(root)
	if e := m.InitRepository(ctx, repo); e != nil {
		t.Fatal(e)
	}
	objects, e := local.New(root)
	if e != nil {
		t.Fatal(e)
	}
	data := []byte("test")
	hash := sha256.Sum256(data)
	ref := object.Ref{Algorithm: "sha256", Digest: fmt.Sprintf("%x", hash), Size: int64(len(data))}
	if _, e = objects.Put(ctx, ref, bytes.NewReader(data)); e != nil {
		t.Fatal(e)
	}
	s := NewService(m, objects)
	d, e := s.PublishDataset(ctx, Command{1, repo, op, "publish_dataset", DatasetBody{Files: []DatasetFile{{"input", ref}}}, nil})
	if e != nil {
		t.Fatal(e)
	}
	material := func(name, purpose, path string) Material {
		return Material{name, purpose, true, path, MaterialSource{Kind: "object", Object: &ref}}
	}
	body := SnapshotBody{ProfileID: "profile", Inputs: []SnapshotInput{{"train", *d.RecordID, "data"}}, Code: Code{"example", "1111111111111111111111111111111111111111", "src/main"}, Parameters: map[string]TypedValue{"seed": {"integer", int64(1)}}, Split: Split{"split", []string{"split"}}, Environment: Environment{"env", []string{"env"}, map[string]TypedValue{"version": {"number", float64(1)}}}, Materials: []Material{material("code", "code", "src/main"), material("split", "split", "split"), material("env", "environment", "env")}}
	sc := Command{1, repo, "00000000000000000000000000000003", "save_snapshot", body, &RefUpdate{"snapshot", 0}}
	snap, e := s.SaveSnapshot(ctx, sc)
	if e != nil {
		t.Fatal(e)
	}
	evidence := "reported"
	run := RunBody{SnapshotID: *snap.RecordID, Outcome: "succeeded", Observations: map[string]Observation{"version": {"passed", &TypedValue{"number", float64(1)}, &evidence}}, Metrics: map[string]Metric{}, Outputs: []Output{}, EvidenceSource: "caller_reported"}
	c := Command{1, repo, "00000000000000000000000000000004", "register_run", run, nil}
	r1, e := s.RegisterRun(ctx, c)
	if e != nil {
		t.Fatal(e)
	}
	again, e := s.RegisterRun(ctx, c)
	if e != nil || *again.RecordID != *r1.RecordID || again.Sequence != r1.Sequence {
		t.Fatal(again, e)
	}
	c.OperationID = "00000000000000000000000000000005"
	r2, e := s.RegisterRun(ctx, c)
	if e != nil || *r1.RecordID == *r2.RecordID {
		t.Fatal(r2, e)
	}
	c.OperationID = "00000000000000000000000000000006"
	run.Observations["version"] = Observation{"passed", &TypedValue{"integer", int64(1)}, &evidence}
	c.Body = run
	if _, e = s.RegisterRun(ctx, c); !errors.Is(e, ErrConditionMismatch) {
		t.Fatal(e)
	}
	run.Observations = map[string]Observation{}
	run.Outcome = "interrupted"
	reason := "stopped"
	run.Reason = &reason
	run.SourceRunID = r1.RecordID
	c.Body = run
	if _, e = s.RegisterRun(ctx, c); e != nil {
		t.Fatal(e)
	}
	v, e := m.Read(ctx)
	if e != nil || v.Sequence() != 5 {
		t.Fatal(v, e)
	}
	p, e := s.ListRuns(ctx, ListOptions{0, 10, 4})
	if e != nil || len(p.Items) != 2 {
		t.Fatal(p, e)
	}
	if e = os.Remove(filepath.Join(root, "objects", "sha256", ref.Digest)); e != nil {
		t.Fatal(e)
	}
	replay, e := s.SaveSnapshot(ctx, sc)
	if e != nil || *replay.RecordID != *snap.RecordID {
		t.Fatal(replay, e)
	}
	sc.OperationID = "00000000000000000000000000000007"
	if _, e = s.SaveSnapshot(ctx, sc); !errors.Is(e, ErrConflict) {
		t.Fatal("stale CAS", e)
	}
	sc.RefUpdate = nil
	if _, e = s.SaveSnapshot(ctx, sc); !errors.Is(e, ErrMissingDependency) {
		t.Fatal("missing object", e)
	}
	v2, e := m.Read(ctx)
	if e != nil || v2.Sequence() != 5 {
		t.Fatal("failed mutation changed sequence", e)
	}
	old, _ := v.GetRecord(*snap.RecordID)
	b := old.Body.(SnapshotBody)
	b.Parameters["seed"] = TypedValue{"integer", int64(9)}
	old2, _ := v.GetRecord(*snap.RecordID)
	if old2.Body.(SnapshotBody).Parameters["seed"].Value != int64(1) {
		t.Fatal("view aliases")
	}
}
func TestExperimentStrictJSON(t *testing.T) {
	for _, raw := range []string{`{"type":"null","value":null}`, `{"type":"integer","value":1.0}`, `{"type":"number","value":null}`, `{"type":"integer","value":1,"value":2}`, `{"type":"number","value":1e999}`} {
		var v TypedValue
		if strictDecode([]byte(raw), &v) == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
}
