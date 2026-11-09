// Package object defines references to verified immutable bytes.
package object

import (
	"context"
	"errors"
	"fmt"
	"io"
)

var (
	ErrInvalidRef = errors.New("invalid object reference")
	ErrNotFound   = errors.New("object not found")
	ErrIntegrity  = errors.New("object integrity violation")
	ErrStorage    = errors.New("storage error")
	ErrUncertain  = errors.New("uncertain publication outcome")
)

type Ref struct {
	Algorithm string `json:"algorithm"`
	Digest    string `json:"digest"`
	Size      int64  `json:"size"`
}

func (ref Ref) Validate() error {
	if ref.Algorithm != "sha256" || len(ref.Digest) != 64 || ref.Size < 0 {
		return fmt.Errorf("%w: %+v", ErrInvalidRef, ref)
	}
	for _, c := range ref.Digest {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return fmt.Errorf("%w: %+v", ErrInvalidRef, ref)
		}
	}
	return nil
}

type Store interface {
	Put(context.Context, Ref, io.Reader) (Ref, error)
	Verify(context.Context, Ref) error
	Open(context.Context, Ref) (io.ReadCloser, error)
}
