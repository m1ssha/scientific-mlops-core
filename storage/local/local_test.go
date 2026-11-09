package local

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"

	"github.com/m1ssha/scientific-mlops-core/object"
)

func ref(b []byte) object.Ref {
	return object.Ref{Algorithm: "sha256", Digest: fmt.Sprintf("%x", sha256.Sum256(b)), Size: int64(len(b))}
}
func TestObjects(t *testing.T) {
	root := t.TempDir()
	s, e := New(root)
	if e != nil {
		t.Fatal(e)
	}
	ctx := context.Background()
	for _, b := range [][]byte{nil, {0, 255, 1, 0}, bytes.Repeat([]byte("abcdef"), 20000)} {
		r := ref(b)
		if _, e = s.Put(ctx, r, bytes.NewReader(b)); e != nil {
			t.Fatal(e)
		}
		reopened, e := New(root)
		if e != nil {
			t.Fatal(e)
		}
		f, e := reopened.Open(ctx, r)
		if e != nil {
			t.Fatal(e)
		}
		got, e := io.ReadAll(f)
		f.Close()
		if e != nil || !bytes.Equal(got, b) {
			t.Fatal(e)
		}
	}
	r := ref([]byte("hello"))
	if e = s.Verify(ctx, r); !errors.Is(e, object.ErrNotFound) {
		t.Fatal(e)
	}
	bad := r
	bad.Size++
	if _, e = s.Put(ctx, bad, bytes.NewReader([]byte("hello"))); !errors.Is(e, object.ErrIntegrity) {
		t.Fatal(e)
	}
	bad = r
	bad.Digest = ref([]byte("other")).Digest
	if _, e = s.Put(ctx, bad, bytes.NewReader([]byte("hello"))); !errors.Is(e, object.ErrIntegrity) {
		t.Fatal(e)
	}
	bad = r
	bad.Digest = "ABC"
	if e = s.Verify(ctx, bad); !errors.Is(e, object.ErrInvalidRef) {
		t.Fatal(e)
	}
	bad = r
	bad.Size = -1
	if e = s.Verify(ctx, bad); !errors.Is(e, object.ErrInvalidRef) {
		t.Fatal(e)
	}
	fail := errors.New("reader failed")
	if _, e = s.Put(ctx, r, errReader{fail}); !errors.Is(e, fail) {
		t.Fatal(e)
	}
	c, cancel := context.WithCancel(ctx)
	cancel()
	if _, e = s.Put(c, r, bytes.NewReader([]byte("hello"))); !errors.Is(e, context.Canceled) {
		t.Fatal(e)
	}
	if _, e = s.Put(ctx, r, bytes.NewReader([]byte("hello"))); e != nil {
		t.Fatal(e)
	}
	p := s.path(r)
	if e = os.WriteFile(p, []byte("wrong"), 0600); e != nil {
		t.Fatal(e)
	}
	wrongSize := r
	wrongSize.Size++
	if e = s.Verify(ctx, wrongSize); !errors.Is(e, object.ErrIntegrity) {
		t.Fatal(e)
	}
	if e = s.Verify(ctx, r); !errors.Is(e, object.ErrIntegrity) {
		t.Fatal(e)
	}
	if _, e = s.Open(ctx, r); !errors.Is(e, object.ErrIntegrity) {
		t.Fatal(e)
	}
	if _, e = s.Put(ctx, r, bytes.NewReader([]byte("hello"))); !errors.Is(e, object.ErrIntegrity) {
		t.Fatal(e)
	}
	b, e := os.ReadFile(p)
	if e != nil || string(b) != "wrong" {
		t.Fatal(e)
	}
}

type errReader struct{ err error }

func (r errReader) Read([]byte) (int, error) { return 0, r.err }
func TestUnsafePaths(t *testing.T) {
	for _, kind := range []string{"symlink", "directory", "fifo"} {
		t.Run(kind, func(t *testing.T) {
			s, e := New(t.TempDir())
			if e != nil {
				t.Fatal(e)
			}
			r := ref([]byte("x"))
			p := s.path(r)
			switch kind {
			case "symlink":
				e = os.Symlink(filepath.Join(t.TempDir(), "target"), p)
			case "directory":
				e = os.Mkdir(p, 0700)
			case "fifo":
				e = exec.Command("mkfifo", p).Run()
			}
			if e != nil {
				t.Fatal(e)
			}
			if e = s.Verify(context.Background(), r); !errors.Is(e, object.ErrStorage) {
				t.Fatal(e)
			}
			if _, e = s.Put(context.Background(), r, bytes.NewReader([]byte("x"))); !errors.Is(e, object.ErrStorage) {
				t.Fatal(e)
			}
		})
	}
	root := t.TempDir()
	os.Symlink(t.TempDir(), filepath.Join(root, "objects"))
	if _, e := New(root); !errors.Is(e, object.ErrStorage) {
		t.Fatal(e)
	}
}
func TestConcurrent(t *testing.T) {
	root := t.TempDir()
	s, e := New(root)
	if e != nil {
		t.Fatal(e)
	}
	b := bytes.Repeat([]byte("x"), 100000)
	r := ref(b)
	var wg sync.WaitGroup
	for range 20 {
		wg.Go(func() {
			if _, e := s.Put(context.Background(), r, bytes.NewReader(b)); e != nil {
				t.Error(e)
			}
		})
	}
	wg.Wait()
	if e = s.Verify(context.Background(), r); e != nil {
		t.Fatal(e)
	}
	// The test executable acts as an independent process sharing the same store.
	root2 := t.TempDir()
	var cmds []*exec.Cmd
	for range 4 {
		c := exec.Command(os.Args[0], "-test.run=^TestProcessHelper$")
		c.Env = append(os.Environ(), "SML_TEST_ROOT="+root2)
		if e = c.Start(); e != nil {
			t.Fatal(e)
		}
		cmds = append(cmds, c)
	}
	for _, c := range cmds {
		if e = c.Wait(); e != nil {
			t.Fatal(e)
		}
	}
	s, e = New(root2)
	if e != nil {
		t.Fatal(e)
	}
	if e = s.Verify(context.Background(), ref([]byte("process"))); e != nil {
		t.Fatal(e)
	}
}
func TestProcessHelper(t *testing.T) {
	root := os.Getenv("SML_TEST_ROOT")
	if root == "" {
		return
	}
	s, e := New(root)
	if e != nil {
		t.Fatal(e)
	}
	b := []byte("process")
	if _, e = s.Put(context.Background(), ref(b), bytes.NewReader(b)); e != nil {
		t.Fatal(e)
	}
}

type gateReader struct {
	entered chan struct{}
	proceed chan struct{}
	done    bool
}

func (r *gateReader) Read(p []byte) (int, error) {
	if r.done {
		return 0, io.EOF
	}
	r.done = true
	close(r.entered)
	<-r.proceed
	return copy(p, []byte("pending")), nil
}
func TestNoPartialVisibility(t *testing.T) {
	s, e := New(t.TempDir())
	if e != nil {
		t.Fatal(e)
	}
	r := ref([]byte("pending"))
	g := &gateReader{entered: make(chan struct{}), proceed: make(chan struct{})}
	done := make(chan error, 1)
	go func() { _, e := s.Put(context.Background(), r, g); done <- e }()
	<-g.entered
	if e = s.Verify(context.Background(), r); !errors.Is(e, object.ErrNotFound) {
		t.Fatal(e)
	}
	close(g.proceed)
	if e = <-done; e != nil {
		t.Fatal(e)
	}
}
