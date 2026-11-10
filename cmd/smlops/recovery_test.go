package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/m1ssha/scientific-mlops-core/catalog"
	"github.com/m1ssha/scientific-mlops-core/object"
	"github.com/m1ssha/scientific-mlops-core/storage/local"
)

func TestRecoveryCLI(t *testing.T) {
	root := t.TempDir()
	ctx := context.Background()
	id := func(i int) catalog.ID { return catalog.ID(fmt.Sprintf("%032x", i)) }
	m, _ := catalog.NewLocalMetadata(root)
	if err := m.InitRepository(ctx, id(1)); err != nil {
		t.Fatal(err)
	}
	obj, err := local.New(root)
	if err != nil {
		t.Fatal(err)
	}
	data := []byte("material")
	ref := object.Ref{Algorithm: "sha256", Digest: fmt.Sprintf("%x", sha256.Sum256(data)), Size: int64(len(data))}
	if _, err = obj.Put(ctx, ref, bytes.NewReader(data)); err != nil {
		t.Fatal(err)
	}
	s := catalog.NewService(m, obj)
	d, err := s.PublishDataset(ctx, catalog.Command{SchemaVersion: 1, RepositoryID: id(1), OperationID: id(2), Type: "publish_dataset", Body: catalog.DatasetBody{Files: []catalog.DatasetFile{{Path: "input", Object: ref}}}})
	if err != nil {
		t.Fatal(err)
	}
	src := catalog.MaterialSource{Kind: "object", Object: &ref}
	body := catalog.SnapshotBody{ProfileID: "p", Inputs: []catalog.SnapshotInput{{Role: "train", DatasetID: *d.RecordID, TargetPrefix: "data"}}, Code: catalog.Code{Repository: "example", Commit: "1111111111111111111111111111111111111111", EntryPoint: "code"}, Parameters: map[string]catalog.TypedValue{}, Split: catalog.Split{Description: "split", Materials: []string{"split"}}, Environment: catalog.Environment{Description: "env", Materials: []string{"env"}, Conditions: map[string]catalog.TypedValue{}}, Materials: []catalog.Material{{Name: "code", Purpose: "code", Required: true, Path: "code", Source: src}, {Name: "split", Purpose: "split", Required: true, Path: "split", Source: src}, {Name: "env", Purpose: "environment", Required: true, Path: "env", Source: src}}}
	sn, err := s.SaveSnapshot(ctx, catalog.Command{SchemaVersion: 1, RepositoryID: id(1), OperationID: id(3), Type: "save_snapshot", Body: body})
	if err != nil {
		t.Fatal(err)
	}
	rn, err := s.RegisterRun(ctx, catalog.Command{SchemaVersion: 1, RepositoryID: id(1), OperationID: id(4), Type: "register_run", Body: catalog.RunBody{SnapshotID: *sn.RecordID, Outcome: "succeeded", Observations: map[string]catalog.Observation{}, Metrics: map[string]catalog.Metric{}, Outputs: []catalog.Output{}, EvidenceSource: "caller_reported"}})
	if err != nil {
		t.Fatal(err)
	}
	// Remove the entire object layout: planning must neither recreate it nor staging.
	if err = os.RemoveAll(filepath.Join(root, "objects")); err != nil {
		t.Fatal(err)
	}
	if err = os.RemoveAll(filepath.Join(root, "staging")); err != nil {
		t.Fatal(err)
	}
	capture := func(args ...string) (int, []byte, []byte) {
		t.Helper()
		out, _ := os.CreateTemp(t.TempDir(), "out")
		defer out.Close()
		er, _ := os.CreateTemp(t.TempDir(), "err")
		defer er.Close()
		oldOut, oldErr := os.Stdout, os.Stderr
		os.Stdout, os.Stderr = out, er
		code := run(args)
		os.Stdout, os.Stderr = oldOut, oldErr
		out.Seek(0, 0)
		er.Seek(0, 0)
		ob, _ := os.ReadFile(out.Name())
		eb, _ := os.ReadFile(er.Name())
		return code, ob, eb
	}
	code, out, stderr := capture("recovery", "plan", "--store", root, "--run", string(*rn.RecordID))
	var plan catalog.RecoveryPlan
	if code != 0 || len(stderr) != 0 || json.Unmarshal(out, &plan) != nil || plan.SourceRunID != *rn.RecordID || len(plan.Blockers) != 4 {
		t.Fatalf("%d %s %s", code, out, stderr)
	}
	for _, dir := range []string{"objects", "staging"} {
		if _, err = os.Stat(filepath.Join(root, dir)); !os.IsNotExist(err) {
			t.Fatalf("created %s: %v", dir, err)
		}
	}
	for _, args := range [][]string{{"recovery", "plan"}, {"recovery", "other"}, {"recovery", "plan", "--store", root, "--run", "bad"}} {
		code, out, stderr = capture(args...)
		var diagnostic struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		if code != 2 || len(out) != 0 || json.Unmarshal(stderr, &diagnostic) != nil || diagnostic.Error.Code != "invalid_input" {
			t.Fatalf("%v %d %s %s", args, code, out, stderr)
		}
	}
	code, _, stderr = capture("recovery", "plan", "--store", root, "--run", string(id(99)))
	var diagnostic struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	if code != 1 || json.Unmarshal(stderr, &diagnostic) != nil || diagnostic.Error.Code != "not_found" {
		t.Fatal(code, string(stderr))
	}
}
