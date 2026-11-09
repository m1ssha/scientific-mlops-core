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
