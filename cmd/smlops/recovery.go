package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"

	"github.com/m1ssha/scientific-mlops-core/catalog"
	"github.com/m1ssha/scientific-mlops-core/storage/local"
)

func runRecovery(args []string) int {
	if len(args) == 0 || args[0] != "plan" {
		return fail("invalid_input", errors.New("expected recovery plan"))
	}
	fs := flag.NewFlagSet("recovery plan", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	root := fs.String("store", "", "")
	runID := fs.String("run", "", "")
	if err := fs.Parse(args[1:]); err != nil {
		return fail("invalid_input", err)
	}
	if fs.NArg() != 0 || *root == "" || !catalog.ValidID(catalog.ID(*runID)) {
		return fail("invalid_input", catalog.ErrInvalidInput)
	}
	meta, err := catalog.NewLocalMetadata(*root)
	if err != nil {
		return fail(classify(err), err)
	}
	objects, err := local.OpenReadOnly(*root)
	if err != nil {
		return fail(classify(err), err)
	}
	plan, err := catalog.NewService(meta, objects).PlanRecovery(context.Background(), catalog.ID(*runID))
	if err != nil {
		return fail(classify(err), err)
	}
	if err = json.NewEncoder(os.Stdout).Encode(plan); err != nil {
		return fail("storage", err)
	}
	return 0
}
