package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strconv"

	"github.com/m1ssha/scientific-mlops-core/object"
	"github.com/m1ssha/scientific-mlops-core/storage/local"
)

type diagnostic struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func fail(code string, err error) int {
	_ = json.NewEncoder(os.Stderr).Encode(struct {
		Error diagnostic `json:"error"`
	}{diagnostic{code, err.Error()}})
	if code == "invalid_input" {
		return 2
	}
	return 1
}
func classify(err error) string {
	switch {
	case errors.Is(err, context.Canceled):
		return "cancelled"
	case errors.Is(err, context.DeadlineExceeded):
		return "deadline"
	case errors.Is(err, object.ErrInvalidRef):
		return "invalid_input"
	case errors.Is(err, object.ErrNotFound):
		return "not_found"
	case errors.Is(err, object.ErrIntegrity):
		return "integrity"
	case errors.Is(err, object.ErrUncertain):
		return "uncertain"
	default:
		return "storage"
	}
}
func run(args []string) int {
	if len(args) < 2 || args[0] != "object" || (args[1] != "put" && args[1] != "verify" && args[1] != "get") {
		return fail("invalid_input", errors.New("expected object put, verify or get"))
	}
	cmd := args[1]
	fs := flag.NewFlagSet(cmd, flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	root := fs.String("store", "", "store directory")
	digest := fs.String("sha256", "", "digest")
	size := fs.String("size", "", "byte count")
	var file, output *string
	if cmd == "put" {
		file = fs.String("file", "", "input path")
	}
	if cmd == "get" {
		output = fs.String("output", "", "new output path")
	}
	if err := fs.Parse(args[2:]); err != nil {
		return fail("invalid_input", err)
	}
	if fs.NArg() != 0 || *root == "" || *size == "" || (file != nil && *file == "") || (output != nil && *output == "") {
		return fail("invalid_input", errors.New("missing or unexpected arguments"))
	}
	n, err := strconv.ParseInt(*size, 10, 64)
	if err != nil {
		return fail("invalid_input", err)
	}
	ref := object.Ref{Algorithm: "sha256", Digest: *digest, Size: n}
	if err = ref.Validate(); err != nil {
		return fail("invalid_input", err)
	}
	store, err := local.New(*root)
	if err != nil {
		return fail(classify(err), err)
	}
	ctx := context.Background()
	switch cmd {
	case "put":
		f, e := os.Open(*file)
		if e != nil {
			return fail("storage", e)
		}
		_, err = store.Put(ctx, ref, f)
		closeErr := f.Close()
		if err == nil {
			err = closeErr
		}
	case "verify":
		err = store.Verify(ctx, ref)
	case "get":
		var in io.ReadCloser
		in, err = store.Open(ctx, ref)
		if err == nil {
			var out *os.File
			out, err = os.OpenFile(*output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if err != nil {
				in.Close()
				if errors.Is(err, os.ErrExist) {
					return fail("conflict", err)
				}
			} else {
				_, err = io.Copy(out, in)
				closeErr := out.Close()
				if err == nil {
					err = closeErr
				}
				inErr := in.Close()
				if err == nil {
					err = inErr
				}
				if err != nil {
					_ = os.Remove(*output)
				}
			}
		}
	}
	if err != nil {
		return fail(classify(err), err)
	}
	result := struct {
		Object object.Ref `json:"object"`
		Output string     `json:"output,omitempty"`
	}{Object: ref}
	if output != nil {
		result.Output = *output
	}
	if err = json.NewEncoder(os.Stdout).Encode(result); err != nil {
		return fail("storage", fmt.Errorf("write output: %w", err))
	}
	return 0
}
func main() { os.Exit(run(os.Args[1:])) }
