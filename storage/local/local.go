// Package local implements a single-host, trusted-owner object store.
package local

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/m1ssha/scientific-mlops-core/object"
)

type Store struct{ root string }

func storage(err error) error { return fmt.Errorf("%w: %w", object.ErrStorage, err) }
func dir(path string) error {
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		if err = os.Mkdir(path, 0700); err != nil && !errors.Is(err, os.ErrExist) {
			return storage(err)
		}
		info, err = os.Lstat(path)
	}
	if err != nil {
		return storage(err)
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return storage(fmt.Errorf("unsafe directory %q", path))
	}
	return nil
}
func New(root string) (*Store, error) {
	if root == "" {
		return nil, fmt.Errorf("%w: empty root", object.ErrInvalidRef)
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, storage(err)
	}
	// Reject symbolic links in every existing component, including parents.
	current := string(filepath.Separator)
	for _, part := range split(abs) {
		current = filepath.Join(current, part)
		if err := dir(current); err != nil {
			return nil, err
		}
	}
	s := &Store{root: abs}
	if err := s.layout(); err != nil {
		return nil, err
	}
	return s, nil
}
func split(path string) []string {
	var parts []string
	for path != string(filepath.Separator) && path != "." {
		parts = append([]string{filepath.Base(path)}, parts...)
		path = filepath.Dir(path)
	}
	return parts
}
func (s *Store) layout() error {
	for _, p := range []string{s.root, filepath.Join(s.root, "objects"), filepath.Join(s.root, "objects", "sha256"), filepath.Join(s.root, "staging")} {
		if err := dir(p); err != nil {
			return err
		}
	}
	return nil
}
func (s *Store) path(ref object.Ref) string {
	return filepath.Join(s.root, "objects", "sha256", ref.Digest)
}
func check(ctx context.Context, r io.Reader) (string, int64, error) {
	h := sha256.New()
	buf := make([]byte, 32*1024)
	var n int64
	for {
		if err := ctx.Err(); err != nil {
			return "", n, err
		}
		count, err := r.Read(buf)
		if count > 0 {
			_, _ = h.Write(buf[:count])
			n += int64(count)
		}
		if err == io.EOF {
			if e := ctx.Err(); e != nil {
				return "", n, e
			}
			return hex.EncodeToString(h.Sum(nil)), n, nil
		}
		if err != nil {
			return "", n, err
		}
	}
}
func compare(ref object.Ref, digest string, size int64) error {
	if digest != ref.Digest || size != ref.Size {
		return fmt.Errorf("%w: object %s expected size %d, got digest %s size %d", object.ErrIntegrity, ref.Digest, ref.Size, digest, size)
	}
	return nil
}
func (s *Store) open(ctx context.Context, ref object.Ref) (*os.File, error) {
	if err := ref.Validate(); err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := s.layout(); err != nil {
		return nil, err
	}
	path := s.path(ref)
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("%w: object %s", object.ErrNotFound, ref.Digest)
	}
	if err != nil {
		return nil, storage(err)
	}
	if !info.Mode().IsRegular() {
		return nil, storage(fmt.Errorf("unsafe object path %q", path))
	}
	f, err := os.Open(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, fmt.Errorf("%w: object %s", object.ErrNotFound, ref.Digest)
		}
		return nil, storage(err)
	}
	info, err = f.Stat()
	if err != nil {
		f.Close()
		return nil, storage(err)
	}
	if !info.Mode().IsRegular() {
		f.Close()
		return nil, storage(fmt.Errorf("nonregular object %s", ref.Digest))
	}
	digest, size, err := check(ctx, f)
	if err != nil {
		f.Close()
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, storage(err)
	}
	if err = compare(ref, digest, size); err != nil {
		f.Close()
		return nil, err
	}
	if _, err = f.Seek(0, io.SeekStart); err != nil {
		f.Close()
		return nil, storage(err)
	}
	return f, nil
}
func (s *Store) Verify(ctx context.Context, ref object.Ref) error {
	f, err := s.open(ctx, ref)
	if err != nil {
		return err
	}
	if err = f.Close(); err != nil {
		return storage(err)
	}
	return nil
}
func (s *Store) Open(ctx context.Context, ref object.Ref) (io.ReadCloser, error) {
	return s.open(ctx, ref)
}
func (s *Store) Put(ctx context.Context, ref object.Ref, reader io.Reader) (object.Ref, error) {
	var zero object.Ref
	if err := ref.Validate(); err != nil {
		return zero, err
	}
	if reader == nil {
		return zero, fmt.Errorf("%w: nil reader", object.ErrInvalidRef)
	}
	if err := ctx.Err(); err != nil {
		return zero, err
	}
	if err := s.layout(); err != nil {
		return zero, err
	}
	f, err := os.CreateTemp(filepath.Join(s.root, "staging"), "object-")
	if err != nil {
		return zero, storage(err)
	}
	defer os.Remove(f.Name())
	defer f.Close()
	h := sha256.New()
	buf := make([]byte, 32*1024)
	var size int64
	for {
		if err := ctx.Err(); err != nil {
			return zero, err
		}
		n, readErr := reader.Read(buf)
		if n > 0 {
			if _, err = f.Write(buf[:n]); err != nil {
				return zero, storage(err)
			}
			_, _ = h.Write(buf[:n])
			size += int64(n)
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			if ctx.Err() != nil {
				return zero, ctx.Err()
			}
			return zero, storage(readErr)
		}
	}
	if err = ctx.Err(); err != nil {
		return zero, err
	}
	if err = compare(ref, hex.EncodeToString(h.Sum(nil)), size); err != nil {
		return zero, err
	}
	if err = f.Sync(); err != nil {
		return zero, storage(err)
	}
	if err = f.Close(); err != nil {
		return zero, storage(err)
	}
	if err = ctx.Err(); err != nil {
		return zero, err
	}
	if err = os.Link(f.Name(), s.path(ref)); err != nil {
		if errors.Is(err, os.ErrExist) {
			if err = s.Verify(ctx, ref); err != nil {
				return zero, err
			}
			return ref, nil
		}
		return zero, storage(err)
	}
	d, err := os.Open(filepath.Join(s.root, "objects", "sha256"))
	if err != nil {
		return zero, fmt.Errorf("%w: object %s: %v", object.ErrUncertain, ref.Digest, err)
	}
	err = d.Sync()
	closeErr := d.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return zero, fmt.Errorf("%w: object %s: %v", object.ErrUncertain, ref.Digest, err)
	}
	return ref, nil
}
