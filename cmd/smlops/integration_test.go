package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
)

// TestIntegratedWorkflow exercises independent CLI processes against one repository.
func TestIntegratedWorkflow(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "smlops")
	if out, e := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); e != nil {
		t.Fatalf("build: %s %v", out, e)
	}
	root := filepath.Join(dir, "store")
	if e := os.Mkdir(root, 0700); e != nil {
		t.Fatal(e)
	}
	call := func(args ...string) ([]byte, string) {
		t.Helper()
		cmd := exec.Command(bin, args...)
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		out, e := cmd.Output()
		if e == nil {
			return out, ""
		}
		var response struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		if json.Unmarshal(stderr.Bytes(), &response) != nil || response.Error.Code == "" {
			t.Fatalf("%v: %s %v", args, stderr.String(), e)
		}
		return out, response.Error.Code
	}
	ok := func(args ...string) []byte {
		t.Helper()
		out, code := call(args...)
		if code != "" {
			t.Fatalf("%v: %s", args, code)
		}
		return out
	}
	repository := "00000000000000000000000000000001"
	ok("repository", "init", "--store", root, "--repository-id", repository)
	file := filepath.Join(dir, "input")
	data := []byte("integration material")
	if e := os.WriteFile(file, data, 0600); e != nil {
		t.Fatal(e)
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(data))
	size := fmt.Sprint(len(data))
	objArgs := []string{"--store", root, "--sha256", digest, "--size", size}
	ok(append(append([]string{"object", "put"}, objArgs...), "--file", file)...)
	ok(append([]string{"object", "verify"}, objArgs...)...)
	outFile := filepath.Join(dir, "restored")
	ok(append(append([]string{"object", "get"}, objArgs...), "--output", outFile)...)
	restored, e := os.ReadFile(outFile)
	if e != nil || !bytes.Equal(restored, data) {
		t.Fatal("object roundtrip", e)
	}
	ref := map[string]any{"algorithm": "sha256", "digest": digest, "size": len(data)}
	request := filepath.Join(dir, "request.json")
	command := func(op, kind string, body any, refUpdate any) []byte {
		t.Helper()
		b, e := json.Marshal(map[string]any{"schema_version": 1, "repository_id": repository, "operation_id": op, "type": kind, "body": body, "ref_update": refUpdate})
		if e != nil {
			t.Fatal(e)
		}
		return b
	}
	invoke := func(kind, action string, b []byte) ([]byte, string) {
		t.Helper()
		if e := os.WriteFile(request, b, 0600); e != nil {
			t.Fatal(e)
		}
		return call(kind, action, "--store", root, "--request", request)
	}
	receipt := func(b []byte) struct {
		ID       string `json:"record_id"`
		Sequence int    `json:"sequence"`
	} {
		t.Helper()
		var r struct {
			ID       string `json:"record_id"`
			Sequence int    `json:"sequence"`
		}
		if json.Unmarshal(b, &r) != nil || r.ID == "" {
			t.Fatalf("receipt %s", b)
		}
		return r
	}
	datasetCmd := command("00000000000000000000000000000002", "publish_dataset", map[string]any{"predecessor_id": nil, "files": []any{map[string]any{"path": "input.bin", "object": ref}}}, map[string]any{"name": "data", "expected_revision": 0})
	d, code := invoke("dataset", "publish", datasetCmd)
	if code != "" {
		t.Fatal(code)
	}
	dataset := receipt(d)
	savedDataset := ok("dataset", "show", "--store", root, "--id", dataset.ID)
	source := map[string]any{"kind": "object", "object": ref}
	material := func(name, purpose, path string) map[string]any {
		return map[string]any{"name": name, "purpose": purpose, "required": true, "path": path, "source": source}
	}
	sb := map[string]any{"predecessor_id": nil, "profile_id": "integration-v1", "inputs": []any{map[string]any{"role": "train", "dataset_id": dataset.ID, "target_prefix": "data"}}, "code": map[string]any{"repository": "example", "commit": "1111111111111111111111111111111111111111", "entry_point": "src/main"}, "parameters": map[string]any{}, "split": map[string]any{"description": "split", "materials": []string{"split"}}, "environment": map[string]any{"description": "env", "materials": []string{"env"}, "conditions": map[string]any{}}, "materials": []any{material("code", "code", "src/main"), material("env", "environment", "env"), material("split", "split", "split")}}
	snapshotCmd := command("00000000000000000000000000000003", "save_snapshot", sb, nil)
	b, code := invoke("snapshot", "create", snapshotCmd)
	if code != "" {
		t.Fatal(code)
	}
	snap := receipt(b)
	rb := map[string]any{"snapshot_id": snap.ID, "source_run_id": nil, "started_at": nil, "finished_at": nil, "outcome": "succeeded", "reason": nil, "observations": map[string]any{}, "metrics": map[string]any{}, "outputs": []any{map[string]any{"path": "model", "role": "model", "media_type": "application/octet-stream", "object": ref}}, "evidence_source": "caller_reported"}
	run1 := command("00000000000000000000000000000004", "register_run", rb, nil)
	b, code = invoke("run", "register", run1)
	if code != "" {
		t.Fatal(code)
	}
	first := receipt(b)
	replay, code := invoke("run", "register", run1)
	if code != "" || !bytes.Equal(b, replay) {
		t.Fatal("retry", code)
	}
	rb["source_run_id"] = first.ID
	run2 := command("00000000000000000000000000000005", "register_run", rb, nil)
	b, code = invoke("run", "register", run2)
	if code != "" {
		t.Fatal(code)
	}
	second := receipt(b)
	if first.ID == second.ID || second.Sequence != first.Sequence+1 {
		t.Fatal("run identity")
	}
	var page struct {
		Items []json.RawMessage `json:"items"`
	}
	if e = json.Unmarshal(ok("run", "list", "--store", root, "--after-sequence", "0", "--limit", "10", "--view-sequence", "3"), &page); e != nil || len(page.Items) != 1 {
		t.Fatal("pinned page", e)
	}
	// Compare the immutable record itself: view_sequence in the enclosing response changes.
	var old, new struct {
		Record json.RawMessage `json:"record"`
	}
	json.Unmarshal(savedDataset, &old)
	json.Unmarshal(ok("dataset", "show", "--store", root, "--id", dataset.ID), &new)
	if !bytes.Equal(old.Record, new.Record) {
		t.Fatal("history changed")
	}
	changed := command("00000000000000000000000000000004", "register_run", rb, nil)
	if _, code = invoke("run", "register", changed); code != "operation_conflict" {
		t.Fatal("conflicting replay", code)
	}
	b, code = invoke("snapshot", "create", command("00000000000000000000000000000010", "save_snapshot", sb, nil))
	if code != "" {
		t.Fatal("second snapshot", code)
	}
	other := receipt(b)
	invalidRun := map[string]any{}
	for k, v := range rb {
		invalidRun[k] = v
	}
	invalidRun["snapshot_id"] = other.ID
	invalidRun["source_run_id"] = first.ID
	if _, code = invoke("run", "register", command("00000000000000000000000000000011", "register_run", invalidRun, nil)); code != "missing_dependency" {
		t.Fatal("source from other snapshot", code)
	}
	invalidRun["source_run_id"] = nil
	invalidRun["observations"] = map[string]any{"unknown": map[string]any{"status": "unchecked", "value": nil, "evidence": nil}}
	if _, code = invoke("run", "register", command("00000000000000000000000000000012", "register_run", invalidRun, nil)); code != "invalid_input" {
		t.Fatal("unknown condition", code)
	}
	// Competing processes use distinct operation IDs and the same expected revision.
	var wg sync.WaitGroup
	codes := make([]string, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			req := filepath.Join(dir, fmt.Sprintf("cas-%d.json", i))
			body := map[string]any{"name": "data", "target_kind": "dataset", "target_id": dataset.ID, "expected_revision": 1}
			c := command(fmt.Sprintf("%032x", 10+i), "set_reference", body, nil)
			if e := os.WriteFile(req, c, 0600); e != nil {
				t.Error(e)
				return
			}
			_, codes[i] = call("ref", "set", "--store", root, "--request", req)
		}(i)
	}
	wg.Wait()
	if !((codes[0] == "" && codes[1] == "conflict") || (codes[1] == "" && codes[0] == "conflict")) {
		t.Fatalf("CAS results %v", codes)
	}
	if e = os.Remove(filepath.Join(root, "objects", "sha256", digest)); e != nil {
		t.Fatal(e)
	}
	if _, code = invoke("snapshot", "create", snapshotCmd); code != "" {
		t.Fatal("retry after loss", code)
	}
	if _, code = invoke("snapshot", "create", command("0000000000000000000000000000000c", "save_snapshot", sb, nil)); code != "missing_dependency" {
		t.Fatal("missing input", code)
	}
	if _, code = invoke("run", "register", command("0000000000000000000000000000000d", "register_run", rb, nil)); code != "missing_dependency" {
		t.Fatal("missing output", code)
	}
	if e = os.WriteFile(filepath.Join(root, "objects", "sha256", digest), bytes.Repeat([]byte{'x'}, len(data)), 0600); e != nil {
		t.Fatal(e)
	}
	if _, code = invoke("snapshot", "create", command("0000000000000000000000000000000e", "save_snapshot", sb, nil)); code != "integrity" {
		t.Fatal("corrupt input", code)
	}
	if _, code = invoke("run", "register", command("0000000000000000000000000000000f", "register_run", rb, nil)); code != "integrity" {
		t.Fatal("corrupt output", code)
	}
	var state struct {
		Sequence int `json:"sequence"`
	}
	if e = json.Unmarshal(ok("repository", "show", "--store", root), &state); e != nil || state.Sequence != 6 {
		t.Fatalf("failed mutations changed sequence: %+v %v", state, e)
	}

	fixtureRoot := filepath.Join(dir, "fixture-store")
	if e = os.Mkdir(fixtureRoot, 0700); e != nil {
		t.Fatal(e)
	}
	ok("repository", "init", "--store", fixtureRoot, "--repository-id", repository)
	fixture, e := os.ReadFile("../../docs/contracts/v1/catalog-example.json")
	if e != nil {
		t.Fatal(e)
	}
	if e = os.WriteFile(filepath.Join(fixtureRoot, "metadata", "catalog.json"), fixture, 0600); e != nil {
		t.Fatal(e)
	}
	before, e := os.ReadFile(filepath.Join(fixtureRoot, "metadata", "catalog.json"))
	if e != nil {
		t.Fatal(e)
	}
	for _, tc := range []struct{ kind, left, right string }{
		{"dataset", "00000000000000000000000000000001", "00000000000000000000000000000006"},
		{"run", "00000000000000000000000000000003", "00000000000000000000000000000004"},
		{"snapshot", "00000000000000000000000000000002", "00000000000000000000000000000002"},
	} {
		out := ok("diff", tc.kind, "--store", fixtureRoot, "--left", tc.left, "--right", tc.right)
		var d struct {
			Kind         string            `json:"kind"`
			ViewSequence int               `json:"view_sequence"`
			Changes      []json.RawMessage `json:"changes"`
		}
		if json.Unmarshal(out, &d) != nil || d.Kind != tc.kind || d.ViewSequence != 6 || d.Changes == nil {
			t.Fatalf("diff: %s", out)
		}
	}
	if _, code = call("diff", "run", "--store", fixtureRoot, "--left", "00000000000000000000000000000001", "--right", "00000000000000000000000000000004"); code != "not_found" {
		t.Fatal(code)
	}
	after, e := os.ReadFile(filepath.Join(fixtureRoot, "metadata", "catalog.json"))
	if e != nil || !bytes.Equal(before, after) {
		t.Fatal("diff changed history", e)
	}
	if _, e = os.Stat(filepath.Join(fixtureRoot, "objects")); !os.IsNotExist(e) {
		t.Fatal("diff opened object store", e)
	}

}
