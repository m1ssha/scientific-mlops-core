package catalog

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"time"

	"errors"
	"github.com/m1ssha/scientific-mlops-core/object"
	"github.com/m1ssha/scientific-mlops-core/storage/local"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

const repo ID = "00000000000000000000000000000001"
const op ID = "00000000000000000000000000000002"

func TestPublishRetryConflictAndHistory(t *testing.T) {
	ctx := context.Background()
	m, e := NewLocalMetadata(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); e != nil {
		t.Fatal(e)
	}
	s := NewService(m, nil)
	cmd := Command{1, repo, op, "publish_dataset", DatasetBody{Files: []DatasetFile{}}, &RefUpdate{"main", 0}}
	r, e := s.PublishDataset(ctx, cmd)
	if e != nil {
		t.Fatal(e)
	}
	again, e := s.PublishDataset(ctx, cmd)
	if e != nil || again.Sequence != r.Sequence || *again.RecordID != *r.RecordID {
		t.Fatalf("retry: %+v %v", again, e)
	}
	cmd.RefUpdate.ExpectedRevision = 1
	if _, e = s.PublishDataset(ctx, cmd); !errors.Is(e, ErrOperationConflict) {
		t.Fatalf("expected operation conflict: %v", e)
	}
	c := Command{1, repo, "00000000000000000000000000000003", "set_reference", SetReferenceBody{"main", Dataset, *r.RecordID, 1}, nil}
	if _, e = s.SetReference(ctx, c); e != nil {
		t.Fatal(e)
	}
	v, e := m.Read(ctx)
	if e != nil {
		t.Fatal(e)
	}
	ref, _ := v.GetReference("main")
	if ref.Revision != 2 || v.Sequence() != 2 {
		t.Fatal(ref, v.Sequence())
	}
	p, e := s.ListDatasets(ctx, ListOptions{0, 1, 1})
	if e != nil || len(p.Items) != 1 {
		t.Fatal(p, e)
	}
}
func TestInitAndCorruption(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	m, e := NewLocalMetadata(dir)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = m.Read(ctx); !errors.Is(e, ErrNotInitialized) {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); !errors.Is(e, ErrConflict) {
		t.Fatal(e)
	}
	p := filepath.Join(dir, "metadata", "catalog.json")
	b, e := os.ReadFile(p)
	if e != nil {
		t.Fatal(e)
	}
	b[len(b)-2] ^= 1
	if e = os.WriteFile(p, b, 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = m.Read(ctx); !errors.Is(e, ErrIntegrity) {
		t.Fatal(e)
	}
}
func TestDuplicateAndNullJSON(t *testing.T) {
	for _, data := range []string{`{"schema_version":1,"schema_version":1}`, `{"schema_version":1,"repository_id":"00000000000000000000000000000001","operation_id":"00000000000000000000000000000002","type":"publish_dataset","body":{"predecessor_id":null,"files":null},"ref_update":null}`} {
		if _, e := DecodeCommand([]byte(data)); !errors.Is(e, ErrInvalidInput) {
			t.Fatal(e)
		}
	}
}

func TestCommitFailureBoundaries(t *testing.T) {
	for _, phase := range []string{"before_rename", "after_rename"} {
		t.Run(phase, func(t *testing.T) {
			ctx := context.Background()
			m, e := NewLocalMetadata(t.TempDir())
			if e != nil {
				t.Fatal(e)
			}
			if e = m.InitRepository(ctx, repo); e != nil {
				t.Fatal(e)
			}
			s := NewService(m, nil)
			cmd := Command{1, repo, op, "publish_dataset", DatasetBody{Files: []DatasetFile{}}, nil}
			m.testHook = func(p string) error {
				if p == phase {
					return errors.New("injected failure")
				}
				return nil
			}
			_, e = s.PublishDataset(ctx, cmd)
			want := ErrStorage
			if phase == "after_rename" {
				want = ErrUncertain
			}
			if !errors.Is(e, want) {
				t.Fatalf("got %v want %v", e, want)
			}
			m.testHook = nil
			v, e := m.Read(ctx)
			if e != nil {
				t.Fatal(e)
			}
			wantSeq := int64(0)
			if phase == "after_rename" {
				wantSeq = 1
			}
			if v.Sequence() != wantSeq {
				t.Fatal(v.Sequence())
			}
			r, e := s.PublishDataset(ctx, cmd)
			if e != nil || r.Sequence != 1 {
				t.Fatal(r, e)
			}
		})
	}
}

func TestConcurrentProcesses(t *testing.T) {
	if root := os.Getenv("CATALOG_TEST_ROOT"); root != "" {
		m, e := NewLocalMetadata(root)
		if e != nil {
			t.Fatal(e)
		}
		s := NewService(m, nil)
		id := ID(os.Getenv("CATALOG_TEST_OP"))
		_, e = s.PublishDataset(context.Background(), Command{1, repo, id, "publish_dataset", DatasetBody{Files: []DatasetFile{}}, &RefUpdate{"main", 0}})
		if e != nil {
			if errors.Is(e, ErrConflict) {
				os.Exit(3)
			}
			t.Fatal(e)
		}
		return
	}
	root := t.TempDir()
	m, e := NewLocalMetadata(root)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(context.Background(), repo); e != nil {
		t.Fatal(e)
	}
	cmds := make([]*exec.Cmd, 2)
	for i := range cmds {
		cmds[i] = exec.Command(os.Args[0], "-test.run=^TestConcurrentProcesses$")
		cmds[i].Env = append(os.Environ(), "CATALOG_TEST_ROOT="+root, "CATALOG_TEST_OP=0000000000000000000000000000000"+string(rune('2'+i)))
		if e = cmds[i].Start(); e != nil {
			t.Fatal(e)
		}
	}
	success, conflicts := 0, 0
	for _, c := range cmds {
		e = c.Wait()
		if e == nil {
			success++
		} else if x, ok := e.(*exec.ExitError); ok && x.ExitCode() == 3 {
			conflicts++
		} else {
			t.Fatal(e)
		}
	}
	if success != 1 || conflicts != 1 {
		t.Fatal(success, conflicts)
	}
	v, e := m.Read(context.Background())
	if e != nil || v.Sequence() != 1 {
		t.Fatal(v, e)
	}
}
func TestWineMaterial(t *testing.T) {
	b, e := os.ReadFile("../examples/wine/manifest.json")
	if e != nil {
		t.Fatal(e)
	}
	sum := sha256.Sum256(b)
	ref := object.Ref{Algorithm: "sha256", Digest: hex.EncodeToString(sum[:]), Size: int64(len(b))}
	root := t.TempDir()
	store, e := local.New(root)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = store.Put(context.Background(), ref, bytes.NewReader(b)); e != nil {
		t.Fatal(e)
	}
	m, e := NewLocalMetadata(root)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(context.Background(), repo); e != nil {
		t.Fatal(e)
	}
	s := NewService(m, store)
	r, e := s.PublishDataset(context.Background(), Command{1, repo, op, "publish_dataset", DatasetBody{Files: []DatasetFile{{"manifest.json", ref}}}, nil})
	if e != nil {
		t.Fatal(e)
	}
	record, e := s.GetDataset(context.Background(), *r.RecordID)
	if e != nil || record.Body.Files[0].Object != ref {
		t.Fatal(record, e)
	}
	if strings.Contains(root, "examples/wine") {
		t.Fatal("test modified original")
	}
}

func TestLockReleasedOnProcessDeath(t *testing.T) {
	if root := os.Getenv("CATALOG_LOCK_ROOT"); root != "" {
		f, e := os.OpenFile(filepath.Join(root, "metadata", "lock"), os.O_RDONLY, 0)
		if e != nil {
			t.Fatal(e)
		}
		if e = syscall.Flock(int(f.Fd()), syscall.LOCK_EX); e != nil {
			t.Fatal(e)
		}
		fmt.Println("locked")
		select {}
	}
	root := t.TempDir()
	m, e := NewLocalMetadata(root)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(context.Background(), repo); e != nil {
		t.Fatal(e)
	}
	c := exec.Command(os.Args[0], "-test.run=^TestLockReleasedOnProcessDeath$")
	c.Env = append(os.Environ(), "CATALOG_LOCK_ROOT="+root)
	out, e := c.StdoutPipe()
	if e != nil {
		t.Fatal(e)
	}
	if e = c.Start(); e != nil {
		t.Fatal(e)
	}
	line, e := bufio.NewReader(out).ReadString('\n')
	if e != nil || line != "locked\n" {
		c.Process.Kill()
		t.Fatal(line, e)
	}
	if e = c.Process.Kill(); e != nil {
		t.Fatal(e)
	}
	_ = c.Wait()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if e = m.Update(ctx, func(Tx) error { return nil }); e != nil {
		t.Fatal(e)
	}
}

func TestABAAndMissingObject(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	objects, e := local.New(root)
	if e != nil {
		t.Fatal(e)
	}
	m, e := NewLocalMetadata(root)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); e != nil {
		t.Fatal(e)
	}
	s := NewService(m, objects)
	bad := object.Ref{Algorithm: "sha256", Digest: strings.Repeat("0", 64), Size: 1}
	_, e = s.PublishDataset(ctx, Command{1, repo, op, "publish_dataset", DatasetBody{Files: []DatasetFile{{"a", bad}}}, nil})
	if !errors.Is(e, ErrMissingDependency) {
		t.Fatal(e)
	}
	v, e := m.Read(ctx)
	if e != nil || v.Sequence() != 0 {
		t.Fatal(v, e)
	}
	mk := func(id ID, p *ID, ref *RefUpdate) Receipt {
		r, e := s.PublishDataset(ctx, Command{1, repo, id, "publish_dataset", DatasetBody{PredecessorID: p, Files: []DatasetFile{}}, ref})
		if e != nil {
			t.Fatal(e)
		}
		return r
	}
	a := mk(op, nil, &RefUpdate{"main", 0})
	b := mk("00000000000000000000000000000003", a.RecordID, &RefUpdate{"main", 1})
	_, e = s.SetReference(ctx, Command{1, repo, "00000000000000000000000000000004", "set_reference", SetReferenceBody{"main", Dataset, *a.RecordID, 2}, nil})
	if e != nil {
		t.Fatal(e)
	}
	_, e = s.SetReference(ctx, Command{1, repo, "00000000000000000000000000000005", "set_reference", SetReferenceBody{"main", Dataset, *b.RecordID, 1}, nil})
	if !errors.Is(e, ErrConflict) {
		t.Fatal(e)
	}
	r, e := s.GetReference(ctx, "main")
	if e != nil || r.Revision != 3 || r.TargetID != *a.RecordID {
		t.Fatal(r, e)
	}
}
func TestPathParentCollision(t *testing.T) {
	obj := object.Ref{Algorithm: "sha256", Digest: strings.Repeat("0", 64), Size: 0}
	_, e := normalizeDataset(DatasetBody{Files: []DatasetFile{{"a", obj}, {"a-b", obj}, {"a/b", obj}}})
	if !errors.Is(e, ErrInvalidInput) {
		t.Fatal(e)
	}
}

func TestReadersSeeCommittedGenerations(t *testing.T) {
	ctx := context.Background()
	m, e := NewLocalMetadata(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); e != nil {
		t.Fatal(e)
	}
	s := NewService(m, nil)
	done := make(chan error, 1)
	go func() {
		for i := 0; i < 30; i++ {
			id := ID(fmt.Sprintf("%032x", i+100))
			_, err := s.PublishDataset(ctx, Command{1, repo, id, "publish_dataset", DatasetBody{Files: []DatasetFile{}}, nil})
			if err != nil {
				done <- err
				return
			}
		}
		done <- nil
	}()
	for {
		select {
		case e := <-done:
			if e != nil {
				t.Fatal(e)
			}
			return
		default:
			v, e := m.Read(ctx)
			if e != nil {
				t.Fatal(e)
			}
			if int64(len(v.ListRecords())) != v.Sequence() {
				t.Fatal("partial generation")
			}
		}
	}
}

func TestIncompleteAndUnsupportedCatalog(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	m, e := NewLocalMetadata(root)
	if e != nil {
		t.Fatal(e)
	}
	if e = os.Mkdir(filepath.Join(root, "metadata"), 0700); e != nil {
		t.Fatal(e)
	}
	if _, e = m.Read(ctx); !errors.Is(e, ErrIntegrity) {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); !errors.Is(e, ErrConflict) {
		t.Fatal(e)
	}
	fixture, e := os.ReadFile("../docs/contracts/v1/catalog-example.json")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = decodeGeneration(fixture); !errors.Is(e, ErrUnsupportedKind) || errors.Is(e, ErrUnsupportedSchema) {
		t.Fatal(e)
	}
}
func TestViewCopiesAndPinnedPages(t *testing.T) {
	ctx := context.Background()
	m, e := NewLocalMetadata(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); e != nil {
		t.Fatal(e)
	}
	s := NewService(m, nil)
	obj := object.Ref{Algorithm: "sha256", Digest: strings.Repeat("0", 64), Size: 0}
	_ = obj
	first, e := s.PublishDataset(ctx, Command{1, repo, op, "publish_dataset", DatasetBody{Files: []DatasetFile{}}, nil})
	if e != nil {
		t.Fatal(e)
	}
	v, e := m.Read(ctx)
	if e != nil {
		t.Fatal(e)
	}
	r, ok := v.GetRecord(*first.RecordID)
	if !ok {
		t.Fatal("missing")
	}
	r.Body.Files = append(r.Body.Files, DatasetFile{Path: "external"})
	r2, _ := v.GetRecord(*first.RecordID)
	if len(r2.Body.Files) != 0 {
		t.Fatal("aliased record")
	}
	_, e = s.PublishDataset(ctx, Command{1, repo, "00000000000000000000000000000003", "publish_dataset", DatasetBody{Files: []DatasetFile{}}, nil})
	if e != nil {
		t.Fatal(e)
	}
	page, e := s.ListDatasets(ctx, ListOptions{0, 1, 1})
	if e != nil || len(page.Items) != 1 || page.NextAfterSequence != nil || page.ViewSequence != 1 {
		t.Fatal(page, e)
	}
}

func TestTransactionExpiresOnEveryCallbackExit(t *testing.T) {
	for _, outcome := range []string{"success", "error", "panic"} {
		t.Run(outcome, func(t *testing.T) {
			ctx := context.Background()
			m, e := NewLocalMetadata(t.TempDir())
			if e != nil {
				t.Fatal(e)
			}
			if e = m.InitRepository(ctx, repo); e != nil {
				t.Fatal(e)
			}
			var saved Tx
			call := func() {
				defer func() {
					if p := recover(); outcome == "panic" && p == nil {
						t.Error("expected panic")
					} else if outcome != "panic" && p != nil {
						t.Errorf("unexpected panic: %v", p)
					}
				}()
				e = m.Update(ctx, func(tx Tx) error {
					saved = tx
					switch outcome {
					case "error":
						return errors.New("callback failed")
					case "panic":
						panic("callback failed")
					}
					return nil
				})
				if outcome == "error" && e == nil {
					t.Error("expected error")
				}
				if outcome == "success" && e != nil {
					t.Error(e)
				}
			}
			call()
			if saved.View() != nil {
				t.Error("closed transaction returned view")
			}
			if e = saved.AddRecord(Record{}); !errors.Is(e, ErrInvalidInput) {
				t.Errorf("AddRecord: %v", e)
			}
			if e = saved.AddOperation(op, Operation{}); !errors.Is(e, ErrInvalidInput) {
				t.Errorf("AddOperation: %v", e)
			}
			if e = saved.CompareAndSwapReference("main", 0, Reference{}); !errors.Is(e, ErrInvalidInput) {
				t.Errorf("CAS: %v", e)
			}
			v, e := m.Read(ctx)
			if e != nil || v.Sequence() != 0 {
				t.Fatalf("unexpected catalog %v %v", v, e)
			}
		})
	}
}
func TestTransactionConcurrentClosure(t *testing.T) {
	ctx := context.Background()
	m, e := NewLocalMetadata(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); e != nil {
		t.Fatal(e)
	}
	s := NewService(m, nil)
	r, e := s.PublishDataset(ctx, Command{1, repo, op, "publish_dataset", DatasetBody{Files: []DatasetFile{}}, nil})
	if e != nil {
		t.Fatal(e)
	}
	ready := make(chan struct{})
	stop := make(chan struct{})
	done := make(chan error, 1)
	var saved Tx
	e = m.Update(ctx, func(tx Tx) error {
		saved = tx
		go func() {
			close(ready)
			for {
				select {
				case <-stop:
					done <- nil
					return
				default:
				}
				_ = tx.View()
				err := tx.AddRecord(Record{ID: *r.RecordID})
				if !errors.Is(err, ErrConflict) && !errors.Is(err, ErrInvalidInput) {
					done <- err
					return
				}
			}
		}()
		<-ready
		return nil
	})
	if e != nil {
		t.Fatal(e)
	}
	close(stop)
	if e = <-done; e != nil {
		t.Fatal(e)
	}
	if saved.View() != nil {
		t.Fatal("closed transaction returned view")
	}
	v, e := m.Read(ctx)
	if e != nil || v.Sequence() != 1 || len(v.ListRecords()) != 1 {
		t.Fatal(v, e)
	}
}

type unavailableObjects struct {
	object.Store
	checks int
}

func (s *unavailableObjects) Verify(context.Context, object.Ref) error {
	s.checks++
	return object.ErrStorage
}
func TestRetryDoesNotAccessUnavailableObjects(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	objects, e := local.New(root)
	if e != nil {
		t.Fatal(e)
	}
	body := []byte("required material")
	sum := sha256.Sum256(body)
	ref := object.Ref{Algorithm: "sha256", Digest: hex.EncodeToString(sum[:]), Size: int64(len(body))}
	if _, e = objects.Put(ctx, ref, bytes.NewReader(body)); e != nil {
		t.Fatal(e)
	}
	m, e := NewLocalMetadata(root)
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); e != nil {
		t.Fatal(e)
	}
	c := Command{1, repo, op, "publish_dataset", DatasetBody{Files: []DatasetFile{{"data", ref}}}, nil}
	first, e := NewService(m, objects).PublishDataset(ctx, c)
	if e != nil {
		t.Fatal(e)
	}
	bad := &unavailableObjects{}
	s := NewService(m, bad)
	again, e := s.PublishDataset(ctx, c)
	if e != nil || !reflect.DeepEqual(first, again) || bad.checks != 0 {
		t.Fatal(again, e, bad.checks)
	}
	changed := c
	changed.RefUpdate = &RefUpdate{"main", 0}
	if _, e = s.PublishDataset(ctx, changed); !errors.Is(e, ErrOperationConflict) || bad.checks != 0 {
		t.Fatal(e, bad.checks)
	}
	c.OperationID = "00000000000000000000000000000003"
	if _, e = s.PublishDataset(ctx, c); !errors.Is(e, ErrStorage) || bad.checks != 1 {
		t.Fatal(e, bad.checks)
	}
}

func TestClosedTransactionCannotChangeCommit(t *testing.T) {
	ctx := context.Background()
	m, e := NewLocalMetadata(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	if e = m.InitRepository(ctx, repo); e != nil {
		t.Fatal(e)
	}
	started := make(chan struct{})
	release := make(chan struct{})
	m.testHook = func(phase string) error {
		if phase == "before_rename" {
			close(started)
			<-release
		}
		return nil
	}
	txCh := make(chan Tx, 1)
	done := make(chan error, 1)
	recordID := ID("00000000000000000000000000000004")
	kind := Dataset
	cmd := Command{1, repo, op, "publish_dataset", DatasetBody{Files: []DatasetFile{}}, nil}
	result := Receipt{SchemaVersion: 1, OperationID: op, RecordKind: &kind, RecordID: &recordID, Sequence: 1}
	go func() {
		done <- m.Update(ctx, func(tx Tx) error {
			txCh <- tx
			if e := tx.AddRecord(Record{1, recordID, Dataset, 1, cmd.Body.(DatasetBody)}); e != nil {
				return e
			}
			return tx.AddOperation(op, Operation{cmd, result})
		})
	}()
	tx := <-txCh
	<-started
	var wg sync.WaitGroup
	errs := make(chan error, 4)
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 100; j++ {
				if tx.View() != nil {
					errs <- errors.New("closed view")
					return
				}
				if e := tx.AddRecord(Record{ID: "00000000000000000000000000000005"}); !errors.Is(e, ErrInvalidInput) {
					errs <- e
					return
				}
				if e := tx.CompareAndSwapReference("x", 0, Reference{}); !errors.Is(e, ErrInvalidInput) {
					errs <- e
					return
				}
			}
		}()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		t.Fatal(e)
	}
	close(release)
	if e = <-done; e != nil {
		t.Fatal(e)
	}
	v, e := m.Read(ctx)
	if e != nil || v.Sequence() != 1 || len(v.ListRecords()) != 1 {
		t.Fatal(v, e)
	}
}
