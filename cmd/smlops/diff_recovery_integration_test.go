package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/m1ssha/scientific-mlops-core/catalog"
	"github.com/m1ssha/scientific-mlops-core/object"
	"github.com/m1ssha/scientific-mlops-core/storage/local"
)

// TestDiffRecoveryHistory exercises both interfaces against the same real objects
// and immutable metadata, without running any ML computation.
func TestDiffRecoveryHistory(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	root := filepath.Join(dir, "store")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(dir, "smlops")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("build: %s: %v", out, err)
	}
	id := func(n int) catalog.ID { return catalog.ID(fmt.Sprintf("%032x", n)) }
	metadata, err := catalog.NewLocalMetadata(root)
	if err != nil {
		t.Fatal(err)
	}
	if err = metadata.InitRepository(ctx, id(1)); err != nil {
		t.Fatal(err)
	}
	objects, err := local.New(root)
	if err != nil {
		t.Fatal(err)
	}
	put := func(data string) object.Ref {
		t.Helper()
		b := []byte(data)
		ref := object.Ref{Algorithm: "sha256", Digest: fmt.Sprintf("%x", sha256.Sum256(b)), Size: int64(len(b))}
		if _, e := objects.Put(ctx, ref, bytes.NewReader(b)); e != nil {
			t.Fatal(e)
		}
		return ref
	}
	oldData := "old input bytes"
	oldRef := put(oldData)
	newRef := put("new input bytes")
	commonRef := put("declared materials")
	outputRef := put("old output bytes")
	svc := catalog.NewService(metadata, objects)
	command := func(n int, typ string, body any, update *catalog.RefUpdate) catalog.Command {
		return catalog.Command{SchemaVersion: 1, RepositoryID: id(1), OperationID: id(n), Type: typ, Body: body, RefUpdate: update}
	}
	oldDataset, err := svc.PublishDataset(ctx, command(2, "publish_dataset", catalog.DatasetBody{Files: []catalog.DatasetFile{{Path: "input.csv", Object: oldRef}}}, &catalog.RefUpdate{Name: "data", ExpectedRevision: 0}))
	if err != nil {
		t.Fatal(err)
	}
	source := catalog.MaterialSource{Kind: "object", Object: &commonRef}
	oldSnapshotBody := catalog.SnapshotBody{ProfileID: "test-v1", Inputs: []catalog.SnapshotInput{{Role: "train", DatasetID: *oldDataset.RecordID, TargetPrefix: "data"}}, Code: catalog.Code{Repository: "example", Commit: strings.Repeat("1", 40), EntryPoint: "src/code"}, Parameters: map[string]catalog.TypedValue{"seed": {Type: "integer", Value: int64(1)}}, Split: catalog.Split{Description: "split", Materials: []string{"split"}}, Environment: catalog.Environment{Description: "env", Materials: []string{"env"}, Conditions: map[string]catalog.TypedValue{}}, Materials: []catalog.Material{{Name: "code", Purpose: "code", Required: true, Path: "src/code", Source: source}, {Name: "env", Purpose: "environment", Required: true, Path: "env", Source: source}, {Name: "split", Purpose: "split", Required: true, Path: "split", Source: source}}}
	oldSnapshot, err := svc.SaveSnapshot(ctx, command(3, "save_snapshot", oldSnapshotBody, &catalog.RefUpdate{Name: "latest", ExpectedRevision: 0}))
	if err != nil {
		t.Fatal(err)
	}
	runBody := func(snap catalog.ID, outputs []catalog.Output) catalog.RunBody {
		return catalog.RunBody{SnapshotID: snap, Outcome: "succeeded", Observations: map[string]catalog.Observation{}, Metrics: map[string]catalog.Metric{}, Outputs: outputs, EvidenceSource: "caller_reported"}
	}
	oldRun, err := svc.RegisterRun(ctx, command(4, "register_run", runBody(*oldSnapshot.RecordID, []catalog.Output{{Path: "result", Role: "result", MediaType: "application/octet-stream", Object: outputRef}}), nil))
	if err != nil {
		t.Fatal(err)
	}
	newDataset, err := svc.PublishDataset(ctx, command(5, "publish_dataset", catalog.DatasetBody{PredecessorID: oldDataset.RecordID, Files: []catalog.DatasetFile{{Path: "input.csv", Object: newRef}}}, &catalog.RefUpdate{Name: "data", ExpectedRevision: 1}))
	if err != nil {
		t.Fatal(err)
	}
	newBody := oldSnapshotBody
	newBody.PredecessorID = oldSnapshot.RecordID
	newBody.Inputs = []catalog.SnapshotInput{{Role: "train", DatasetID: *newDataset.RecordID, TargetPrefix: "data"}}
	newBody.Parameters = map[string]catalog.TypedValue{"seed": {Type: "integer", Value: int64(2)}}
	newSnapshot, err := svc.SaveSnapshot(ctx, command(6, "save_snapshot", newBody, &catalog.RefUpdate{Name: "latest", ExpectedRevision: 1}))
	if err != nil {
		t.Fatal(err)
	}
	newRun, err := svc.RegisterRun(ctx, command(7, "register_run", runBody(*newSnapshot.RecordID, []catalog.Output{}), nil))
	if err != nil {
		t.Fatal(err)
	}
	catalogPath := filepath.Join(root, "metadata", "catalog.json")
	baseline, err := os.ReadFile(catalogPath)
	if err != nil {
		t.Fatal(err)
	}
	initial, err := metadata.Read(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if initial.Sequence() != 6 {
		t.Fatal(initial.Sequence())
	}
	ids := []catalog.ID{*oldDataset.RecordID, *oldSnapshot.RecordID, *oldRun.RecordID, *newDataset.RecordID, *newSnapshot.RecordID, *newRun.RecordID}
	records := make([]catalog.Record, len(ids))
	for i, id := range ids {
		var ok bool
		records[i], ok = initial.GetRecord(id)
		if !ok {
			t.Fatal(id)
		}
	}
	refs := map[string]catalog.Reference{}
	for _, name := range []string{"data", "latest"} {
		r, ok := initial.GetReference(name)
		if !ok {
			t.Fatal(name)
		}
		refs[name] = r
	}
	operations := make([]catalog.Operation, 6)
	for i := range operations {
		var ok bool
		operations[i], ok = initial.GetOperation(id(i + 2))
		if !ok {
			t.Fatal(i)
		}
	}
	objectState := func() map[string]string {
		t.Helper()
		state := map[string]string{}
		for _, ref := range []object.Ref{oldRef, newRef, commonRef, outputRef} {
			b, e := os.ReadFile(filepath.Join(root, "objects", "sha256", ref.Digest))
			if os.IsNotExist(e) {
				state[ref.Digest] = "missing"
				continue
			}
			if e != nil {
				t.Fatal(e)
			}
			state[ref.Digest] = "present:" + string(b)
		}
		return state
	}
	objectBaseline := objectState()
	unchanged := func() {
		t.Helper()
		if !reflect.DeepEqual(objectState(), objectBaseline) {
			t.Fatal("read operation changed object bytes")
		}
		b, e := os.ReadFile(catalogPath)
		if e != nil || !bytes.Equal(b, baseline) {
			t.Fatalf("catalog changed: %v", e)
		}
		v, e := metadata.Read(ctx)
		if e != nil || v.Sequence() != 6 {
			t.Fatalf("view changed: %v", e)
		}
		for i, id := range ids {
			r, ok := v.GetRecord(id)
			if !ok || !reflect.DeepEqual(r, records[i]) {
				t.Fatalf("record changed: %s", id)
			}
		}
		for name, ref := range refs {
			r, ok := v.GetReference(name)
			if !ok || !reflect.DeepEqual(r, ref) {
				t.Fatalf("reference changed: %s", name)
			}
		}
		for i, op := range operations {
			r, ok := v.GetOperation(id(i + 2))
			if !ok || !reflect.DeepEqual(r, op) {
				t.Fatalf("receipt changed: %d", i)
			}
		}
	}
	cli := func(expectSuccess bool, args ...string) []byte {
		t.Helper()
		cmd := exec.Command(bin, args...)
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		out, e := cmd.Output()
		if expectSuccess && (e != nil || stderr.Len() != 0) {
			t.Fatalf("%v: %s %v", args, stderr.String(), e)
		}
		if !expectSuccess && (e == nil || len(out) != 0 || !strings.Contains(stderr.String(), "not_found")) {
			t.Fatalf("expected not_found: %s %s %v", out, stderr.String(), e)
		}
		unchanged()
		return out
	}
	sameJSON := func(a, b any) {
		t.Helper()
		aa, e := json.Marshal(a)
		if e != nil {
			t.Fatal(e)
		}
		bb, e := json.Marshal(b)
		if e != nil {
			t.Fatal(e)
		}
		var x, y any
		if json.Unmarshal(aa, &x) != nil || json.Unmarshal(bb, &y) != nil || !reflect.DeepEqual(x, y) {
			t.Fatalf("different results:\n%s\n%s", aa, bb)
		}
	}
	for _, pair := range [][2]catalog.ID{{*oldRun.RecordID, *newRun.RecordID}, {*newRun.RecordID, *oldRun.RecordID}} {
		d, e := svc.CompareRuns(ctx, pair[0], pair[1])
		if e != nil {
			t.Fatal(e)
		}
		unchanged()
		var cd catalog.Diff
		if e = json.Unmarshal(cli(true, "diff", "run", "--store", root, "--left", string(pair[0]), "--right", string(pair[1])), &cd); e != nil {
			t.Fatal(e)
		}
		sameJSON(d, cd)
		paths := map[string]bool{}
		for _, c := range d.Changes {
			paths[c.Path] = true
			if c.Path == "/conditions/parameters/seed" {
				wantLeft, wantRight := int64(1), int64(2)
				if pair[0] == *newRun.RecordID {
					wantLeft, wantRight = wantRight, wantLeft
				}
				if c.Kind != "changed" || !reflect.DeepEqual(c.Left, catalog.TypedValue{Type: "integer", Value: wantLeft}) || !reflect.DeepEqual(c.Right, catalog.TypedValue{Type: "integer", Value: wantRight}) {
					t.Fatalf("wrong direction: %+v", c)
				}
			}
			if c.Path == "/outputs/result" {
				want := "removed"
				if pair[0] == *newRun.RecordID {
					want = "added"
				}
				if c.Kind != want {
					t.Fatalf("wrong output direction: %+v", c)
				}
			}
		}
		for _, path := range []string{"/conditions/inputs/train", "/conditions/parameters/seed", "/provenance/snapshot_id", "/outputs/result"} {
			if !paths[path] {
				t.Fatalf("missing %s: %+v", path, d)
			}
		}
	}
	cli(false, "diff", "run", "--store", root, "--left", string(*oldRun.RecordID), "--right", string(id(99)))
	if _, e := svc.CompareRuns(ctx, *oldRun.RecordID, id(99)); !errors.Is(e, catalog.ErrNotFound) {
		t.Fatalf("expected missing run: %v", e)
	}
	unchanged()
	cli(false, "recovery", "plan", "--store", root, "--run", string(id(99)))
	if _, e := svc.PlanRecovery(ctx, id(99)); !errors.Is(e, catalog.ErrNotFound) {
		t.Fatalf("expected missing plan: %v", e)
	}
	unchanged()
	oldObjectPath := filepath.Join(root, "objects", "sha256", oldRef.Digest)
	outputPath := filepath.Join(root, "objects", "sha256", outputRef.Digest)
	check := func(expected string, ready bool, outputStatus string) {
		t.Helper()
		// The fixture owner deliberately changes object bytes between read operations.
		objectBaseline = objectState()
		plan, e := svc.PlanRecovery(ctx, *oldRun.RecordID)
		if e != nil {
			t.Fatal(e)
		}
		unchanged()
		var cp catalog.RecoveryPlan
		if e = json.Unmarshal(cli(true, "recovery", "plan", "--store", root, "--run", string(*oldRun.RecordID)), &cp); e != nil {
			t.Fatal(e)
		}
		plan.CheckedAt = cp.CheckedAt
		for i := range plan.Materials {
			plan.Materials[i].CheckedAt = cp.Materials[i].CheckedAt
		}
		sameJSON(plan, cp)
		if plan.SnapshotID != *oldSnapshot.RecordID || plan.ViewSequence != 6 || plan.Conditions.Inputs[0].DatasetID != *oldDataset.RecordID || plan.Conditions.Parameters["seed"].Value != int64(1) || refs["latest"].TargetID != *newSnapshot.RecordID || refs["data"].TargetID != *newDataset.RecordID {
			t.Fatalf("historical plan: %+v", plan)
		}
		if plan.StoredMaterialsReady != ready || plan.RuntimeReady != "unchecked" || plan.Execution != "unchecked" || plan.NumericalAgreement != "unchecked" {
			t.Fatalf("readiness: %+v", plan)
		}
		foundInput, foundOutput := false, false
		for _, m := range plan.Materials {
			if m.Key == "input:train:input.csv" {
				foundInput = true
				if m.Check != expected || !m.Required || m.Source.Object == nil || *m.Source.Object != oldRef {
					t.Fatal(m)
				}
			}
			if m.Key == "output:result" {
				foundOutput = true
				if m.Check != outputStatus || m.Required {
					t.Fatal(m)
				}
			}
		}
		if !foundInput || !foundOutput {
			t.Fatal("missing material")
		}
		if ready && len(plan.Blockers) != 0 {
			t.Fatal(plan.Blockers)
		}
		if !ready && (len(plan.Blockers) != 1 || plan.Blockers[0].MaterialKey != "input:train:input.csv" || plan.Blockers[0].Code != expected) {
			t.Fatal(plan.Blockers)
		}
	}
	check("verified", true, "verified")
	if e := os.Remove(outputPath); e != nil {
		t.Fatal(e)
	}
	check("verified", true, "missing")
	if e := os.Remove(oldObjectPath); e != nil {
		t.Fatal(e)
	}
	check("missing", false, "missing")
	if e := os.WriteFile(oldObjectPath, []byte(oldData), 0600); e != nil {
		t.Fatal(e)
	}
	check("verified", true, "missing")
	if e := os.WriteFile(oldObjectPath, bytes.Repeat([]byte{'x'}, len(oldData)), 0600); e != nil {
		t.Fatal(e)
	}
	check("corrupt", false, "missing")
	if e := os.WriteFile(oldObjectPath, []byte(oldData), 0600); e != nil {
		t.Fatal(e)
	}
	check("verified", true, "missing")
	if e := os.Remove(oldObjectPath); e != nil {
		t.Fatal(e)
	}
	check("missing", false, "missing")
}
