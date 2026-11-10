package catalog

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"time"

	"github.com/m1ssha/scientific-mlops-core/object"
)

type PlanMaterial struct {
	Key       string         `json:"key"`
	Path      string         `json:"path"`
	Purpose   string         `json:"purpose"`
	Required  bool           `json:"required"`
	Source    MaterialSource `json:"source"`
	Check     string         `json:"check"`
	CheckedAt *time.Time     `json:"checked_at"`
	Detail    *string        `json:"detail"`
}
type RecoveryBlocker struct {
	MaterialKey string `json:"material_key"`
	Code        string `json:"code"`
	Message     string `json:"message"`
}
type RecoveryAction struct {
	ID          string   `json:"id"`
	Type        string   `json:"type"`
	MaterialKey *string  `json:"material_key"`
	DependsOn   []string `json:"depends_on"`
}
type RecoveryPlan struct {
	SchemaVersion        int               `json:"schema_version"`
	RepositoryID         ID                `json:"repository_id"`
	ViewSequence         int64             `json:"view_sequence"`
	SourceRunID          ID                `json:"source_run_id"`
	SnapshotID           ID                `json:"snapshot_id"`
	CheckedAt            time.Time         `json:"checked_at"`
	Coverage             string            `json:"coverage"`
	Conditions           SnapshotBody      `json:"conditions"`
	Materials            []PlanMaterial    `json:"materials"`
	Blockers             []RecoveryBlocker `json:"blockers"`
	Actions              []RecoveryAction  `json:"actions"`
	StoredMaterialsReady bool              `json:"stored_materials_ready"`
	RuntimeReady         string            `json:"runtime_ready"`
	Execution            string            `json:"execution"`
	NumericalAgreement   string            `json:"numerical_agreement"`
}

// PlanRecovery checks the declared materials against one immutable metadata view.
// It does not reserve, materialize, execute, or repair any material.
func (s *Service) PlanRecovery(ctx context.Context, runID ID) (RecoveryPlan, error) {
	var zero RecoveryPlan
	if !validID(runID) {
		return zero, ErrInvalidInput
	}
	if err := ctx.Err(); err != nil {
		return zero, err
	}
	v, err := s.metadata.Read(ctx)
	if err != nil {
		return zero, err
	}
	run, ok := v.GetRecord(runID)
	if !ok || run.Kind != Run {
		return zero, ErrNotFound
	}
	rb, ok := run.Body.(RunBody)
	if !ok {
		return zero, ErrIntegrity
	}
	snap, ok := v.GetRecord(rb.SnapshotID)
	if !ok || snap.Kind != Snapshot {
		return zero, ErrIntegrity
	}
	sb, ok := snap.Body.(SnapshotBody)
	if !ok {
		return zero, ErrIntegrity
	}
	p := RecoveryPlan{SchemaVersion: 1, RepositoryID: v.RepositoryID(), ViewSequence: v.Sequence(), SourceRunID: runID, SnapshotID: rb.SnapshotID, CheckedAt: time.Now().UTC(), Coverage: "declared_only", Conditions: sb, Materials: []PlanMaterial{}, Blockers: []RecoveryBlocker{}, Actions: []RecoveryAction{}, StoredMaterialsReady: true, RuntimeReady: "unchecked", Execution: "unchecked", NumericalAgreement: "unchecked"}
	add := func(m PlanMaterial) { p.Materials = append(p.Materials, m) }
	// QueryEscape encodes colon and percent so concatenated components cannot collide.
	for _, in := range sb.Inputs {
		r, ok := v.GetRecord(in.DatasetID)
		if !ok || r.Kind != Dataset {
			return zero, ErrIntegrity
		}
		ds, ok := r.Body.(DatasetBody)
		if !ok {
			return zero, ErrIntegrity
		}
		for _, f := range ds.Files {
			ref := f.Object
			add(PlanMaterial{Key: "input:" + url.QueryEscape(in.Role) + ":" + url.QueryEscape(f.Path), Path: in.TargetPrefix + "/" + f.Path, Purpose: "input", Required: true, Source: MaterialSource{Kind: "object", Object: &ref}})
		}
	}
	for _, m := range sb.Materials {
		add(PlanMaterial{Key: "material:" + url.QueryEscape(m.Name), Path: m.Path, Purpose: m.Purpose, Required: m.Required, Source: m.Source})
	}
	for _, o := range rb.Outputs {
		ref := o.Object
		add(PlanMaterial{Key: "output:" + url.QueryEscape(o.Path), Path: o.Path, Purpose: "original_output", Source: MaterialSource{Kind: "object", Object: &ref}})
	}
	requiredActions := []string{}
	for i := range p.Materials {
		if err := ctx.Err(); err != nil {
			return zero, err
		}
		m := &p.Materials[i]
		if m.Source.Kind == "external" {
			m.Check = "unchecked"
			d := "external source was not checked"
			m.Detail = &d
		} else {
			if s.objects == nil || m.Source.Object == nil {
				return zero, fmt.Errorf("%w: object store or reference unavailable", ErrIntegrity)
			}
			e := s.objects.Verify(ctx, *m.Source.Object)
			if err := ctx.Err(); err != nil {
				return zero, err
			}
			if errors.Is(e, context.Canceled) || errors.Is(e, context.DeadlineExceeded) {
				return zero, e
			}
			now := time.Now().UTC()
			m.CheckedAt = &now
			switch {
			case e == nil:
				m.Check = "verified"
			case errors.Is(e, object.ErrNotFound):
				m.Check = "missing"
			case errors.Is(e, object.ErrIntegrity):
				m.Check = "corrupt"
			default:
				m.Check = "io_error"
			}
			if e != nil {
				d := e.Error()
				m.Detail = &d
			}
		}
		if m.Required && m.Check != "verified" {
			p.StoredMaterialsReady = false
			p.Blockers = append(p.Blockers, RecoveryBlocker{m.Key, m.Check, *m.Detail})
		}
		obtain := fmt.Sprintf("m%d-obtain", i)
		verify := fmt.Sprintf("m%d-verify", i)
		materialize := fmt.Sprintf("m%d-materialize", i)
		key := m.Key
		p.Actions = append(p.Actions, RecoveryAction{obtain, "obtain", &key, []string{}}, RecoveryAction{verify, "verify", &key, []string{obtain}}, RecoveryAction{materialize, "materialize", &key, []string{verify}})
		if m.Required {
			requiredActions = append(requiredActions, materialize)
		}
	}
	p.Actions = append(p.Actions, RecoveryAction{"prepare_environment", "prepare_environment", nil, requiredActions}, RecoveryAction{"validate_conditions", "validate_conditions", nil, []string{"prepare_environment"}}, RecoveryAction{"execute_trusted", "execute_trusted", nil, []string{"validate_conditions"}}, RecoveryAction{"register_run", "register_run", nil, []string{"execute_trusted"}})
	if err := ctx.Err(); err != nil {
		return zero, err
	}
	return p, nil
}
