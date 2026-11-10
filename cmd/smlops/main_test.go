package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestCLI(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "smlops")
	c := exec.Command("go", "build", "-o", bin, ".")
	if b, e := c.CombinedOutput(); e != nil {
		t.Fatalf("%s: %v", b, e)
	}
	store := filepath.Join(dir, "store")
	input := filepath.Join(dir, "input")
	output := filepath.Join(dir, "out")
	b := []byte("hello\x00")
	os.WriteFile(input, b, 0600)
	digest := fmt.Sprintf("%x", sha256.Sum256(b))
	common := []string{"--store", store, "--sha256", digest, "--size", "6"}
	invoke := func(args []string, code int, errCode string) {
		t.Helper()
		c := exec.Command(bin, args...)
		out, e := c.Output()
		if code == 0 && e != nil {
			t.Fatal(e)
		}
		if code != 0 {
			ex, ok := e.(*exec.ExitError)
			if !ok || ex.ExitCode() != code || len(out) != 0 {
				t.Fatalf("exit %v stdout %s", e, out)
			}
			var v struct {
				Error struct {
					Code string `json:"code"`
				} `json:"error"`
			}
			if e = json.Unmarshal(ex.Stderr, &v); e != nil || v.Error.Code != errCode {
				t.Fatalf("diagnostic %s: %v", ex.Stderr, e)
			}
			return
		}
		var v struct {
			Object struct {
				Digest string `json:"digest"`
			} `json:"object"`
		}
		if e = json.Unmarshal(out, &v); e != nil || v.Object.Digest != digest {
			t.Fatalf("output %s: %v", out, e)
		}
	}
	invoke(append([]string{"object", "verify"}, common...), 1, "not_found")
	invoke(append(append([]string{"object", "put"}, common...), "--file", input), 0, "")
	invoke(append([]string{"object", "verify"}, common...), 0, "")
	invoke(append(append([]string{"object", "get"}, common...), "--output", output), 0, "")
	got, e := os.ReadFile(output)
	if e != nil || string(got) != string(b) {
		t.Fatal(e)
	}
	invoke(append(append([]string{"object", "get"}, common...), "--output", output), 1, "conflict")
	invoke([]string{"object", "bad"}, 2, "invalid_input")
	invoke([]string{"object", "verify", "--unknown"}, 2, "invalid_input")
	os.WriteFile(filepath.Join(store, "objects", "sha256", digest), []byte("broken"), 0600)
	invoke(append(append([]string{"object", "get"}, common...), "--output", filepath.Join(dir, "new")), 1, "integrity")
}

func TestCatalogCLI(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "smlops")
	build := exec.Command("go", "build", "-o", bin, ".")
	if b, e := build.CombinedOutput(); e != nil {
		t.Fatal(string(b), e)
	}
	store := filepath.Join(dir, "store")
	if e := os.Mkdir(store, 0700); e != nil {
		t.Fatal(e)
	}
	invoke := func(args ...string) []byte {
		t.Helper()
		c := exec.Command(bin, args...)
		b, e := c.CombinedOutput()
		if e != nil {
			t.Fatalf("%v: %s %v", args, b, e)
		}
		return b
	}
	id := "00000000000000000000000000000001"
	invoke("repository", "init", "--store", store, "--repository-id", id)
	request := filepath.Join(dir, "request.json")
	body := `{"schema_version":1,"repository_id":"` + id + `","operation_id":"00000000000000000000000000000002","type":"publish_dataset","body":{"predecessor_id":null,"files":[]},"ref_update":{"name":"main","expected_revision":0}}`
	if e := os.WriteFile(request, []byte(body), 0600); e != nil {
		t.Fatal(e)
	}
	b := invoke("dataset", "publish", "--store", store, "--request", request)
	var receipt struct {
		RecordID string `json:"record_id"`
	}
	if e := json.Unmarshal(b, &receipt); e != nil || receipt.RecordID == "" {
		t.Fatal(string(b), e)
	}
	invoke("dataset", "show", "--store", store, "--id", receipt.RecordID)
	invoke("dataset", "list", "--store", store, "--after-sequence", "0", "--limit", "2", "--view-sequence", "1")
	invoke("ref", "show", "--store", store, "--name", "main")
	invoke("operation", "show", "--store", store, "--id", "00000000000000000000000000000002")
}

func TestCatalogCLIRetryWithoutObjectLayout(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "smlops")
	build := exec.Command("go", "build", "-o", bin, ".")
	if b, e := build.CombinedOutput(); e != nil {
		t.Fatalf("build: %s: %v", b, e)
	}
	store := filepath.Join(dir, "store")
	if e := os.Mkdir(store, 0700); e != nil {
		t.Fatal(e)
	}
	run := func(want int, errCode string, args ...string) []byte {
		t.Helper()
		cmd := exec.Command(bin, args...)
		out, e := cmd.Output()
		if want == 0 {
			if e != nil {
				t.Fatalf("%v: %v", args, e)
			}
			return out
		}
		ex, ok := e.(*exec.ExitError)
		if !ok || ex.ExitCode() != want || len(out) != 0 {
			t.Fatalf("%v: %v, stdout %q", args, e, out)
		}
		var d struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		if e = json.Unmarshal(ex.Stderr, &d); e != nil || d.Error.Code != errCode {
			t.Fatalf("%v: %s", args, ex.Stderr)
		}
		return nil
	}
	id := "00000000000000000000000000000001"
	op := "00000000000000000000000000000002"
	run(0, "", "repository", "init", "--store", store, "--repository-id", id)
	material := []byte("test material")
	source := filepath.Join(dir, "material")
	if e := os.WriteFile(source, material, 0600); e != nil {
		t.Fatal(e)
	}
	digest := fmt.Sprintf("%x", sha256.Sum256(material))
	size := fmt.Sprint(len(material))
	run(0, "", "object", "put", "--store", store, "--file", source, "--sha256", digest, "--size", size)
	request := filepath.Join(dir, "request.json")
	writeRequest := func(operation, update string) {
		t.Helper()
		body := fmt.Sprintf(`{"schema_version":1,"repository_id":%q,"operation_id":%q,"type":"publish_dataset","body":{"predecessor_id":null,"files":[{"path":"data","object":{"algorithm":"sha256","digest":%q,"size":%s}}]},"ref_update":%s}`, id, operation, digest, size, update)
		if e := os.WriteFile(request, []byte(body), 0600); e != nil {
			t.Fatal(e)
		}
	}
	writeRequest(op, "null")
	first := run(0, "", "dataset", "publish", "--store", store, "--request", request)
	staging := filepath.Join(store, "staging")
	if e := os.Remove(staging); e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(staging, []byte("broken layout"), 0600); e != nil {
		t.Fatal(e)
	}
	again := run(0, "", "dataset", "publish", "--store", store, "--request", request)
	if string(first) != string(again) {
		t.Fatalf("retry changed receipt: %s %s", first, again)
	}
	writeRequest(op, `{"name":"main","expected_revision":0}`)
	run(1, "operation_conflict", "dataset", "publish", "--store", store, "--request", request)
	writeRequest("00000000000000000000000000000003", "null")
	run(1, "storage", "dataset", "publish", "--store", store, "--request", request)
}

func TestExperimentCLI(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "smlops")
	if out, e := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); e != nil {
		t.Fatalf("%s: %v", out, e)
	}
	store := filepath.Join(dir, "store")
	if e := os.Mkdir(store, 0700); e != nil {
		t.Fatal(e)
	}
	invoke := func(args ...string) []byte {
		t.Helper()
		out, e := exec.Command(bin, args...).CombinedOutput()
		if e != nil {
			t.Fatalf("%v: %s %v", args, out, e)
		}
		return out
	}
	repo := "00000000000000000000000000000001"
	invoke("repository", "init", "--store", store, "--repository-id", repo)
	req := filepath.Join(dir, "request.json")
	write := func(s string) {
		t.Helper()
		if e := os.WriteFile(req, []byte(s), 0600); e != nil {
			t.Fatal(e)
		}
	}
	write(`{"schema_version":1,"repository_id":"` + repo + `","operation_id":"00000000000000000000000000000002","type":"publish_dataset","body":{"predecessor_id":null,"files":[]},"ref_update":null}`)
	var rec struct {
		RecordID string `json:"record_id"`
	}
	if e := json.Unmarshal(invoke("dataset", "publish", "--store", store, "--request", req), &rec); e != nil {
		t.Fatal(e)
	}
	dataset := rec.RecordID
	body := `{"predecessor_id":null,"profile_id":"test","inputs":[{"role":"input","dataset_id":"` + dataset + `","target_prefix":"data"}],"code":{"repository":"example","commit":"1111111111111111111111111111111111111111","entry_point":"src/main"},"parameters":{},"split":{"description":"split","materials":["split"]},"environment":{"description":"env","materials":["env"],"conditions":{}},"materials":[{"name":"code","purpose":"code","required":true,"path":"src/main","source":{"kind":"external","locator":"example:code","expected_object":null}},{"name":"env","purpose":"environment","required":true,"path":"env","source":{"kind":"external","locator":"example:env","expected_object":null}},{"name":"split","purpose":"split","required":true,"path":"split","source":{"kind":"external","locator":"example:split","expected_object":null}}]}`
	write(`{"schema_version":1,"repository_id":"` + repo + `","operation_id":"00000000000000000000000000000003","type":"save_snapshot","body":` + body + `,"ref_update":null}`)
	if e := json.Unmarshal(invoke("snapshot", "create", "--store", store, "--request", req), &rec); e != nil {
		t.Fatal(e)
	}
	snapshot := rec.RecordID
	invoke("snapshot", "show", "--store", store, "--id", snapshot)
	invoke("snapshot", "list", "--store", store, "--after-sequence", "0", "--limit", "10", "--view-sequence", "2")
	write(`{"schema_version":1,"repository_id":"` + repo + `","operation_id":"00000000000000000000000000000004","type":"register_run","body":{"snapshot_id":"` + snapshot + `","source_run_id":null,"started_at":null,"finished_at":null,"outcome":"interrupted","reason":"stopped","observations":{},"metrics":{},"outputs":[],"evidence_source":"caller_reported"},"ref_update":null}`)
	first := invoke("run", "register", "--store", store, "--request", req)
	second := invoke("run", "register", "--store", store, "--request", req)
	if string(first) != string(second) {
		t.Fatal("retry changed receipt")
	}
	if e := json.Unmarshal(first, &rec); e != nil {
		t.Fatal(e)
	}
	invoke("run", "show", "--store", store, "--id", rec.RecordID)
	invoke("run", "list", "--store", store, "--after-sequence", "0", "--limit", "10", "--view-sequence", "3")
}
