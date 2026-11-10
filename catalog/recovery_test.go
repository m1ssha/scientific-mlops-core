package catalog

import (
	"context"
	"errors"
	"fmt"
	"io"
	"testing"

	"github.com/m1ssha/scientific-mlops-core/object"
)

type recoveryMetadata struct {
	view  View
	err   error
	reads int
}

func (m *recoveryMetadata) Read(context.Context) (View, error)           { m.reads++; return m.view, m.err }
func (m *recoveryMetadata) Update(context.Context, func(Tx) error) error { panic("unexpected write") }

type recoveryObjects struct {
	failures map[string]error
	calls    []string
}

func (o *recoveryObjects) Verify(_ context.Context, r object.Ref) error {
	o.calls = append(o.calls, r.Digest)
	return o.failures[r.Digest]
}
func (*recoveryObjects) Put(context.Context, object.Ref, io.Reader) (object.Ref, error) {
	panic("unexpected write")
}
func (*recoveryObjects) Open(context.Context, object.Ref) (io.ReadCloser, error) {
	panic("unexpected read")
}
func TestRecoveryPlan(t *testing.T) {
	id := func(i int) ID { return ID(fmt.Sprintf("%032x", i)) }
	ref := func(i int) object.Ref {
		return object.Ref{Algorithm: "sha256", Digest: fmt.Sprintf("%064x", i), Size: 1}
	}
	source := func(i int) MaterialSource { r := ref(i); return MaterialSource{Kind: "object", Object: &r} }
	expected := ref(10)
	b := SnapshotBody{ProfileID: "historical", Inputs: []SnapshotInput{{"a:b", id(1), "one"}, {"a", id(2), "two"}}, Parameters: map[string]TypedValue{}, Environment: Environment{Conditions: map[string]TypedValue{}}, Materials: []Material{{"bad", "environment", true, "bad", source(3)}, {"external", "dependency", true, "external", MaterialSource{Kind: "external", Locator: "https://example.invalid", ExpectedObject: &expected}}, {"io", "split", true, "io", source(4)}}}
	run := RunBody{SnapshotID: id(3), Observations: map[string]Observation{}, Metrics: map[string]Metric{}, Outputs: []Output{{Path: "result", Object: ref(5)}}}
	records := map[ID]Record{id(1): {ID: id(1), Kind: Dataset, Body: DatasetBody{Files: []DatasetFile{{Path: "c", Object: ref(1)}}}}, id(2): {ID: id(2), Kind: Dataset, Body: DatasetBody{Files: []DatasetFile{{Path: "b:c", Object: ref(2)}}}}, id(3): {ID: id(3), Kind: Snapshot, Body: b}, id(4): {ID: id(4), Kind: Run, Body: run}, id(5): {ID: id(5), Kind: Snapshot, Body: SnapshotBody{ProfileID: "latest", Inputs: []SnapshotInput{}, Materials: []Material{}, Parameters: map[string]TypedValue{}, Environment: Environment{Conditions: map[string]TypedValue{}}}}}
	m := &recoveryMetadata{view: snapshot{generation{Repository: id(99), Seq: 5, Records: records}}}
	o := &recoveryObjects{failures: map[string]error{ref(2).Digest: object.ErrNotFound, ref(3).Digest: object.ErrIntegrity, ref(4).Digest: errors.New("permission denied"), ref(5).Digest: object.ErrNotFound}}
	p, err := NewService(m, o).PlanRecovery(context.Background(), id(4))
	if err != nil {
		t.Fatal(err)
	}
	if m.reads != 1 || p.Conditions.ProfileID != "historical" || p.StoredMaterialsReady || p.Coverage != "declared_only" || len(p.Blockers) != 4 {
		t.Fatalf("plan %+v", p)
	}
	want := []string{"verified", "missing", "corrupt", "unchecked", "io_error", "missing"}
	keys := map[string]bool{}
	for i, x := range p.Materials {
		if x.Check != want[i] || keys[x.Key] || (x.CheckedAt == nil) != (x.Check == "unchecked") {
			t.Fatalf("material %+v", x)
		}
		keys[x.Key] = true
	}
	if len(o.calls) != 5 {
		t.Fatalf("external verified: %v", o.calls)
	}
	seen := map[string]bool{}
	for _, a := range p.Actions {
		if seen[a.ID] {
			t.Fatal("duplicate action")
		}
		for _, d := range a.DependsOn {
			if !seen[d] {
				t.Fatalf("forward dependency %s", d)
			}
		}
		seen[a.ID] = true
	}
	prep := p.Actions[len(p.Actions)-4]
	if len(prep.DependsOn) != 5 {
		t.Fatalf("optional output is dependency: %+v", prep)
	}
	o.failures = map[string]error{ref(5).Digest: object.ErrNotFound}
	p, err = NewService(m, o).PlanRecovery(context.Background(), id(4))
	if err != nil || p.StoredMaterialsReady {
		t.Fatal("external remains unchecked", err)
	}
	records[id(3)] = Record{ID: id(3), Kind: Snapshot, Body: func() SnapshotBody { c := b; c.Materials = c.Materials[:0]; return c }()}
	p, err = NewService(m, o).PlanRecovery(context.Background(), id(4))
	if err != nil || !p.StoredMaterialsReady {
		t.Fatal("optional output blocks", err)
	}
	o.failures[ref(1).Digest] = object.ErrNotFound
	p, err = NewService(m, o).PlanRecovery(context.Background(), id(4))
	if err != nil || p.StoredMaterialsReady {
		t.Fatal("lost material cached", err)
	}
	if m.view.Sequence() != 5 {
		t.Fatal("history modified")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err = NewService(m, o).PlanRecovery(ctx, id(4)); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	m.err = ErrIntegrity
	if _, err = NewService(m, o).PlanRecovery(context.Background(), id(4)); !errors.Is(err, ErrIntegrity) {
		t.Fatal(err)
	}
}
