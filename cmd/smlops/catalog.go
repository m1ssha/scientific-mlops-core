package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"strconv"
	"sync"

	"github.com/m1ssha/scientific-mlops-core/catalog"
	"github.com/m1ssha/scientific-mlops-core/object"
	"github.com/m1ssha/scientific-mlops-core/storage/local"
)

// deferredObjectStore leaves the object layout untouched until a new command
// needs byte verification. Saved operations are resolved without opening it.
type deferredObjectStore struct {
	root  string
	once  sync.Once
	store *local.Store
	err   error
}

func (s *deferredObjectStore) open() (*local.Store, error) {
	s.once.Do(func() { s.store, s.err = local.New(s.root) })
	return s.store, s.err
}
func (s *deferredObjectStore) Verify(ctx context.Context, ref object.Ref) error {
	store, err := s.open()
	if err != nil {
		return err
	}
	return store.Verify(ctx, ref)
}
func (s *deferredObjectStore) Put(ctx context.Context, ref object.Ref, r io.Reader) (object.Ref, error) {
	store, err := s.open()
	if err != nil {
		return object.Ref{}, err
	}
	return store.Put(ctx, ref, r)
}
func (s *deferredObjectStore) Open(ctx context.Context, ref object.Ref) (io.ReadCloser, error) {
	store, err := s.open()
	if err != nil {
		return nil, err
	}
	return store.Open(ctx, ref)
}

func runCatalog(args []string) int {
	if len(args) < 2 {
		return fail("invalid_input", errors.New("expected catalog command"))
	}
	kind, action := args[0], args[1]
	valid := kind == "repository" && (action == "init" || action == "show") || kind == "dataset" && (action == "publish" || action == "show" || action == "list") || kind == "snapshot" && (action == "create" || action == "show" || action == "list") || kind == "run" && (action == "register" || action == "show" || action == "list") || kind == "ref" && (action == "show" || action == "set") || kind == "operation" && action == "show" || kind == "diff" && (action == "dataset" || action == "snapshot" || action == "run")
	if !valid {
		return fail("invalid_input", errors.New("unknown catalog command"))
	}
	fs := flag.NewFlagSet(action, flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	root := fs.String("store", "", "")
	var id, name, request, after, limit, view, left, right *string
	if kind == "diff" {
		left = fs.String("left", "", "")
		right = fs.String("right", "", "")
	}
	if kind == "repository" && action == "init" {
		id = fs.String("repository-id", "", "")
	}
	if (kind == "dataset" || kind == "snapshot" || kind == "run" || kind == "operation") && action == "show" {
		id = fs.String("id", "", "")
	}
	if kind == "ref" && action == "show" {
		name = fs.String("name", "", "")
	}
	if action == "publish" || action == "create" || action == "register" || kind == "ref" && action == "set" {
		request = fs.String("request", "", "")
	}
	if action == "list" {
		after = fs.String("after-sequence", "", "")
		limit = fs.String("limit", "", "")
		view = fs.String("view-sequence", "", "")
	}
	if e := fs.Parse(args[2:]); e != nil {
		return fail("invalid_input", e)
	}
	if fs.NArg() != 0 || *root == "" || id != nil && *id == "" || name != nil && *name == "" || request != nil && *request == "" {
		return fail("invalid_input", errors.New("missing or unexpected arguments"))
	}
	if kind == "diff" && (!catalog.ValidID(catalog.ID(*left)) || !catalog.ValidID(catalog.ID(*right))) {
		return fail("invalid_input", catalog.ErrInvalidInput)
	}
	if id != nil && !catalog.ValidID(catalog.ID(*id)) {
		return fail("invalid_input", catalog.ErrInvalidInput)
	}
	var parsed catalog.Command
	if request != nil {
		data, err := os.ReadFile(*request)
		if err != nil {
			return fail("storage", err)
		}
		parsed, err = catalog.DecodeCommand(data)
		if err != nil {
			return fail(classify(err), err)
		}
	}
	ctx := context.Background()
	store, e := catalog.NewLocalMetadata(*root)
	if e != nil {
		return fail(classify(e), e)
	}
	var result any
	if kind == "repository" && action == "init" {
		e = store.InitRepository(ctx, catalog.ID(*id))
		if e == nil {
			result = struct {
				SchemaVersion int        `json:"schema_version"`
				RepositoryID  catalog.ID `json:"repository_id"`
				Sequence      int64      `json:"sequence"`
			}{1, catalog.ID(*id), 0}
		}
	} else {
		if kind == "diff" {
			result, e = catalog.NewService(store, nil).Compare(ctx, catalog.RecordKind(action), catalog.ID(*left), catalog.ID(*right))
		} else {
			v, err := store.Read(ctx)
			if err != nil {
				return fail(classify(err), err)
			}
			service := catalog.NewService(store, &deferredObjectStore{root: *root})
			switch {
			case kind == "repository":
				result = struct {
					SchemaVersion int        `json:"schema_version"`
					RepositoryID  catalog.ID `json:"repository_id"`
					Sequence      int64      `json:"sequence"`
				}{1, v.RepositoryID(), v.Sequence()}
			case request != nil:
				if kind == "dataset" {
					result, e = service.PublishDataset(ctx, parsed)
				} else if kind == "snapshot" {
					result, e = service.SaveSnapshot(ctx, parsed)
				} else if kind == "run" {
					result, e = service.RegisterRun(ctx, parsed)
				} else {
					result, e = service.SetReference(ctx, parsed)
				}
			case kind == "operation":
				result, e = service.GetOperation(ctx, catalog.ID(*id))
			case kind == "ref":
				var r catalog.Reference
				r0, ok := v.GetReference(*name)
				r = r0
				if !ok {
					e = catalog.ErrNotFound
				} else {
					result = struct {
						SchemaVersion int                     `json:"schema_version"`
						ViewSequence  int64                   `json:"view_sequence"`
						Reference     catalog.ReferenceResult `json:"reference"`
					}{1, v.Sequence(), catalog.ReferenceResult{Name: *name, TargetKind: r.TargetKind, TargetID: r.TargetID, Revision: r.Revision}}
				}
			case (kind == "dataset" || kind == "snapshot" || kind == "run") && action == "show":
				r, ok := v.GetRecord(catalog.ID(*id))
				if !ok || string(r.Kind) != kind {
					e = catalog.ErrNotFound
				} else {
					result = struct {
						SchemaVersion int            `json:"schema_version"`
						ViewSequence  int64          `json:"view_sequence"`
						Record        catalog.Record `json:"record"`
					}{1, v.Sequence(), r}
				}
			case action == "list":
				var a, l, w int64
				if *after == "" || *limit == "" || *view == "" {
					e = catalog.ErrInvalidInput
					break
				}
				a, e = strconv.ParseInt(*after, 10, 64)
				if e != nil {
					e = catalog.ErrInvalidInput
					break
				}
				l, e = strconv.ParseInt(*limit, 10, 64)
				if e != nil {
					e = catalog.ErrInvalidInput
					break
				}
				w, e = strconv.ParseInt(*view, 10, 64)
				if e != nil {
					e = catalog.ErrInvalidInput
					break
				}
				opts := catalog.ListOptions{AfterSequence: a, Limit: l, ViewSequence: w}
				switch kind {
				case "dataset":
					result, e = service.ListDatasets(ctx, opts)
				case "snapshot":
					result, e = service.ListSnapshots(ctx, opts)
				case "run":
					result, e = service.ListRuns(ctx, opts)
				}
			}
		}
	}
	if e != nil {
		return fail(classify(e), e)
	}
	if e = json.NewEncoder(os.Stdout).Encode(result); e != nil {
		if request != nil || kind == "repository" && action == "init" {
			return fail("uncertain", e)
		}
		return fail("storage", e)
	}
	return 0
}
