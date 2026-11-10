package catalog

import (
	"context"
	"math"
	"reflect"
	"sort"
	"strings"
)

// Diff describes differences in one committed catalog generation.
type Diff struct {
	SchemaVersion int        `json:"schema_version"`
	ViewSequence  int64      `json:"view_sequence"`
	Kind          RecordKind `json:"kind"`
	LeftID        ID         `json:"left_id"`
	RightID       ID         `json:"right_id"`
	Changes       []Change   `json:"changes"`
}

// Change compares saved values. Kind distinguishes an absent key from null.
type Change struct {
	Path  string   `json:"path"`
	Kind  string   `json:"kind"`
	Left  any      `json:"left"`
	Right any      `json:"right"`
	Delta *float64 `json:"delta"`
}

// Compare reads exactly one metadata view and does not inspect object bytes.
func (s *Service) Compare(ctx context.Context, kind RecordKind, left, right ID) (Diff, error) {
	if (kind != Dataset && kind != Snapshot && kind != Run) || !validID(left) || !validID(right) {
		return Diff{}, ErrInvalidInput
	}
	v, err := s.metadata.Read(ctx)
	if err != nil {
		return Diff{}, err
	}
	l, lok := v.GetRecord(left)
	r, rok := v.GetRecord(right)
	if !lok || !rok || l.Kind != kind || r.Kind != kind {
		return Diff{}, ErrNotFound
	}
	d := Diff{1, v.Sequence(), kind, left, right, []Change{}}
	var lp, rp map[string]any
	var profilesEqual bool
	switch kind {
	case Dataset:
		lb, ok := l.Body.(DatasetBody)
		if !ok {
			return Diff{}, ErrIntegrity
		}
		rb, ok := r.Body.(DatasetBody)
		if !ok {
			return Diff{}, ErrIntegrity
		}
		lp, rp = datasetProjection(lb), datasetProjection(rb)
	case Snapshot:
		lb, ok := l.Body.(SnapshotBody)
		if !ok {
			return Diff{}, ErrIntegrity
		}
		rb, ok := r.Body.(SnapshotBody)
		if !ok {
			return Diff{}, ErrIntegrity
		}
		lp, rp = snapshotProjection(lb), snapshotProjection(rb)
	case Run:
		lb, ok := l.Body.(RunBody)
		if !ok {
			return Diff{}, ErrIntegrity
		}
		rb, ok := r.Body.(RunBody)
		if !ok {
			return Diff{}, ErrIntegrity
		}
		ls, lok := v.GetRecord(lb.SnapshotID)
		rs, rok := v.GetRecord(rb.SnapshotID)
		if !lok || !rok || ls.Kind != Snapshot || rs.Kind != Snapshot {
			return Diff{}, ErrIntegrity
		}
		lsb, lok := ls.Body.(SnapshotBody)
		rsb, rok := rs.Body.(SnapshotBody)
		if !lok || !rok {
			return Diff{}, ErrIntegrity
		}
		profilesEqual = lsb.ProfileID == rsb.ProfileID
		lp, rp = runProjection(lb, lsb), runProjection(rb, rsb)
	}
	compareMaps(&d.Changes, "", lp, rp, profilesEqual)
	sort.Slice(d.Changes, func(i, j int) bool {
		if d.Changes[i].Path == d.Changes[j].Path {
			return d.Changes[i].Kind < d.Changes[j].Kind
		}
		return d.Changes[i].Path < d.Changes[j].Path
	})
	return d, nil
}
func (s *Service) CompareDatasets(ctx context.Context, left, right ID) (Diff, error) {
	return s.Compare(ctx, Dataset, left, right)
}
func (s *Service) CompareSnapshots(ctx context.Context, left, right ID) (Diff, error) {
	return s.Compare(ctx, Snapshot, left, right)
}
func (s *Service) CompareRuns(ctx context.Context, left, right ID) (Diff, error) {
	return s.Compare(ctx, Run, left, right)
}

func datasetProjection(b DatasetBody) map[string]any {
	files := map[string]any{}
	for _, f := range b.Files {
		files[f.Path] = f.Object
	}
	return map[string]any{"files": files}
}
func snapshotProjection(b SnapshotBody) map[string]any {
	inputs := map[string]any{}
	for _, i := range b.Inputs {
		inputs[i.Role] = struct {
			DatasetID    ID     `json:"dataset_id"`
			TargetPrefix string `json:"target_prefix"`
		}{i.DatasetID, i.TargetPrefix}
	}
	materials := map[string]any{}
	for _, m := range b.Materials {
		materials[m.Name] = m
	}
	params := map[string]any{}
	for k, v := range b.Parameters {
		params[k] = v
	}
	conditions := map[string]any{}
	for k, v := range b.Environment.Conditions {
		conditions[k] = v
	}
	return map[string]any{"profile_id": b.ProfileID, "inputs": inputs, "code": map[string]any{"repository": b.Code.Repository, "commit": b.Code.Commit, "entry_point": b.Code.EntryPoint}, "parameters": params, "split": map[string]any{"description": b.Split.Description, "materials": b.Split.Materials}, "environment": map[string]any{"description": b.Environment.Description, "materials": b.Environment.Materials, "conditions": conditions}, "materials": materials}
}
func runProjection(b RunBody, s SnapshotBody) map[string]any {
	obs := map[string]any{}
	for k, v := range b.Observations {
		obs[k] = v
	}
	metrics := map[string]any{}
	for k, v := range b.Metrics {
		metrics[k] = v
	}
	outputs := map[string]any{}
	for _, o := range b.Outputs {
		outputs[o.Path] = struct {
			Role      string `json:"role"`
			MediaType string `json:"media_type"`
			Object    any    `json:"object"`
		}{o.Role, o.MediaType, o.Object}
	}
	return map[string]any{"conditions": snapshotProjection(s), "provenance": map[string]any{"snapshot_id": b.SnapshotID, "source_run_id": b.SourceRunID}, "timing": map[string]any{"started_at": b.StartedAt, "finished_at": b.FinishedAt}, "outcome": b.Outcome, "reason": b.Reason, "evidence_source": b.EvidenceSource, "observations": obs, "metrics": metrics, "outputs": outputs}
}
func compareMaps(changes *[]Change, prefix string, left, right map[string]any, profilesEqual bool) {
	keys := map[string]bool{}
	for k := range left {
		keys[k] = true
	}
	for k := range right {
		keys[k] = true
	}
	for k := range keys {
		path := prefix + "/" + strings.ReplaceAll(strings.ReplaceAll(k, "~", "~0"), "/", "~1")
		l, lok := left[k]
		r, rok := right[k]
		if !lok {
			*changes = append(*changes, Change{path, "added", nil, r, nil})
			continue
		}
		if !rok {
			*changes = append(*changes, Change{path, "removed", l, nil, nil})
			continue
		}
		if lm, ok := l.(map[string]any); ok {
			if rm, ok := r.(map[string]any); ok {
				compareMaps(changes, path, lm, rm, profilesEqual)
				continue
			}
		}
		if prefix == "/metrics" {
			lm, lok := l.(Metric)
			rm, rok := r.(Metric)
			if lok && rok {
				delta := rm.Value - lm.Value
				if !profilesEqual || lm.Definition != rm.Definition || lm.Unit != rm.Unit || math.IsInf(delta, 0) || math.IsNaN(delta) {
					*changes = append(*changes, Change{path, "incomparable", l, r, nil})
					continue
				}
				if !reflect.DeepEqual(l, r) {
					*changes = append(*changes, Change{path, "changed", l, r, &delta})
				}
				continue
			}
		}
		if !reflect.DeepEqual(l, r) {
			*changes = append(*changes, Change{path, "changed", l, r, nil})
		}
	}
}
