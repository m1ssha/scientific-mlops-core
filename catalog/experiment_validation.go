package catalog

import (
	"encoding/json"
	"math"
	"reflect"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

func validTyped(v TypedValue) bool {
	switch v.Type {
	case "null":
		return v.Value == nil
	case "integer":
		_, ok := v.Value.(int64)
		return ok
	case "number":
		x, ok := v.Value.(float64)
		return ok && !math.IsNaN(x) && !math.IsInf(x, 0)
	case "string":
		x, ok := v.Value.(string)
		return ok && utf8.ValidString(x)
	case "boolean":
		_, ok := v.Value.(bool)
		return ok
	}
	return false
}
func normalizeTypedMap(m map[string]TypedValue) (map[string]TypedValue, error) {
	if m == nil {
		return nil, ErrInvalidInput
	}
	n := make(map[string]TypedValue, len(m))
	for k, v := range m {
		if !validName(k) || !validTyped(v) {
			return nil, ErrInvalidInput
		}
		if v.Type == "number" && v.Value.(float64) == 0 {
			v.Value = float64(0)
		}
		n[k] = v
	}
	return n, nil
}
func noCollisions(paths []string) bool {
	seen := map[string]bool{}
	for _, p := range paths {
		if !validPath(p) || seen[p] {
			return false
		}
		seen[p] = true
	}
	for p := range seen {
		for i, c := range p {
			if c == '/' && seen[p[:i]] {
				return false
			}
		}
	}
	return true
}
func normalizeSnapshot(b SnapshotBody) (SnapshotBody, error) {
	if b.PredecessorID != nil && !validID(*b.PredecessorID) || !validName(b.ProfileID) || len(b.Inputs) == 0 || b.Materials == nil || !validName(b.Code.Repository) || !validPath(b.Code.EntryPoint) || len(b.Code.Commit) != 40 && len(b.Code.Commit) != 64 {
		return b, ErrInvalidInput
	}
	for _, c := range b.Code.Commit {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return b, ErrInvalidInput
		}
	}
	var err error
	b.Parameters, err = normalizeTypedMap(b.Parameters)
	if err != nil {
		return b, err
	}
	b.Environment.Conditions, err = normalizeTypedMap(b.Environment.Conditions)
	if err != nil {
		return b, err
	}
	if !validName(b.Split.Description) || !validName(b.Environment.Description) || len(b.Split.Materials) == 0 || len(b.Environment.Materials) == 0 {
		return b, ErrInvalidInput
	}
	b.Inputs = append([]SnapshotInput{}, b.Inputs...)
	sort.Slice(b.Inputs, func(i, j int) bool { return b.Inputs[i].Role < b.Inputs[j].Role })
	for i, x := range b.Inputs {
		if !validName(x.Role) || !validID(x.DatasetID) || !validPath(x.TargetPrefix) || i > 0 && b.Inputs[i-1].Role == x.Role {
			return b, ErrInvalidInput
		}
	}
	b.Materials = append([]Material{}, b.Materials...)
	sort.Slice(b.Materials, func(i, j int) bool { return b.Materials[i].Name < b.Materials[j].Name })
	names := map[string]Material{}
	paths := []string{}
	entry := false
	for _, m := range b.Materials {
		if !validName(m.Name) || !validPath(m.Path) || names[m.Name].Name != "" {
			return b, ErrInvalidInput
		}
		switch m.Purpose {
		case "code", "split", "environment", "dependency", "configuration":
		default:
			return b, ErrInvalidInput
		}
		switch m.Source.Kind {
		case "object":
			if m.Source.Object == nil || m.Source.Object.Validate() != nil || m.Source.Locator != "" || m.Source.ExpectedObject != nil {
				return b, ErrInvalidInput
			}
		case "external":
			if !validName(m.Source.Locator) || m.Source.Object != nil || m.Source.ExpectedObject != nil && m.Source.ExpectedObject.Validate() != nil {
				return b, ErrInvalidInput
			}
		default:
			return b, ErrInvalidInput
		}
		if m.Purpose == "code" && m.Required && m.Path == b.Code.EntryPoint {
			entry = true
		}
		names[m.Name] = m
		paths = append(paths, m.Path)
	}
	if !entry || !noCollisions(paths) {
		return b, ErrInvalidInput
	}
	check := func(s []string, purpose string) ([]string, bool) {
		s = append([]string{}, s...)
		sort.Strings(s)
		for i, n := range s {
			m, ok := names[n]
			if !ok || !m.Required || (m.Purpose != purpose && !(purpose == "environment" && m.Purpose == "dependency")) || i > 0 && s[i-1] == n {
				return nil, false
			}
		}
		return s, true
	}
	var ok bool
	b.Split.Materials, ok = check(b.Split.Materials, "split")
	if !ok {
		return b, ErrInvalidInput
	}
	b.Environment.Materials, ok = check(b.Environment.Materials, "environment")
	if !ok {
		return b, ErrInvalidInput
	}
	return cloneBody(b).(SnapshotBody), nil
}
func normalizeRun(b RunBody) (RunBody, error) {
	if !validID(b.SnapshotID) || b.SourceRunID != nil && !validID(*b.SourceRunID) || b.Observations == nil || b.Metrics == nil || b.Outputs == nil || b.EvidenceSource != "caller_reported" {
		return b, ErrInvalidInput
	}
	switch b.Outcome {
	case "succeeded":
		if b.Reason != nil {
			return b, ErrInvalidInput
		}
	case "failed", "interrupted":
		if b.Reason == nil || !validName(*b.Reason) {
			return b, ErrInvalidInput
		}
	default:
		return b, ErrInvalidInput
	}
	var times [2]time.Time
	for i, p := range []*string{b.StartedAt, b.FinishedAt} {
		if p != nil {
			t, e := time.Parse(time.RFC3339Nano, *p)
			if e != nil || !strings.HasSuffix(*p, "Z") {
				return b, ErrInvalidInput
			}
			times[i] = t
		}
	}
	if b.StartedAt != nil && b.FinishedAt != nil && times[1].Before(times[0]) {
		return b, ErrInvalidInput
	}
	observations := make(map[string]Observation, len(b.Observations))
	for k, o := range b.Observations {
		if !validName(k) || o.Value != nil && !validTyped(*o.Value) || o.Evidence != nil && !validName(*o.Evidence) {
			return b, ErrInvalidInput
		}
		switch o.Status {
		case "passed":
			if o.Value == nil || o.Evidence == nil {
				return b, ErrInvalidInput
			}
		case "failed":
			if o.Evidence == nil {
				return b, ErrInvalidInput
			}
		case "unchecked":
			if o.Value != nil {
				return b, ErrInvalidInput
			}
		default:
			return b, ErrInvalidInput
		}
		if o.Value != nil {
			v := *o.Value
			if v.Type == "number" && v.Value.(float64) == 0 {
				v.Value = float64(0)
			}
			o.Value = &v
		}
		observations[k] = o
	}
	b.Observations = observations

	metrics := make(map[string]Metric, len(b.Metrics))
	for k, m := range b.Metrics {
		if !validName(k) || !validName(m.Definition) || !validName(m.Unit) || math.IsNaN(m.Value) || math.IsInf(m.Value, 0) {
			return b, ErrInvalidInput
		}
		if m.Value == 0 {
			m.Value = 0
		}
		metrics[k] = m
	}
	b.Metrics = metrics
	b.Outputs = append([]Output{}, b.Outputs...)
	sort.Slice(b.Outputs, func(i, j int) bool { return b.Outputs[i].Path < b.Outputs[j].Path })
	paths := []string{}
	for _, o := range b.Outputs {
		if !validName(o.Role) || !validName(o.MediaType) || o.Object.Validate() != nil {
			return b, ErrInvalidInput
		}
		paths = append(paths, o.Path)
	}
	if !noCollisions(paths) {
		return b, ErrInvalidInput
	}
	return cloneBody(b).(RunBody), nil
}
func cloneBody(b any) any {
	data, e := json.Marshal(b)
	if e != nil {
		return nil
	}
	var out any
	switch b.(type) {
	case DatasetBody:
		out = &DatasetBody{}
	case SnapshotBody:
		out = &SnapshotBody{}
	case RunBody:
		out = &RunBody{}
	default:
		return nil
	}
	if json.Unmarshal(data, out) != nil {
		return nil
	}
	return reflect.ValueOf(out).Elem().Interface()
}
func checkSnapshotRefs(v View, b SnapshotBody) error {
	if b.PredecessorID != nil {
		r, ok := v.GetRecord(*b.PredecessorID)
		if !ok || r.Kind != Snapshot {
			return ErrMissingDependency
		}
	}
	paths := []string{}
	for _, m := range b.Materials {
		paths = append(paths, m.Path)
	}
	for _, i := range b.Inputs {
		r, ok := v.GetRecord(i.DatasetID)
		if !ok || r.Kind != Dataset {
			return ErrMissingDependency
		}
		for _, f := range r.Body.(DatasetBody).Files {
			paths = append(paths, i.TargetPrefix+"/"+f.Path)
		}
	}
	if !noCollisions(paths) {
		return ErrInvalidInput
	}
	return nil
}
func checkRunRefs(v View, b RunBody) error {
	r, ok := v.GetRecord(b.SnapshotID)
	if !ok || r.Kind != Snapshot {
		return ErrMissingDependency
	}
	if b.SourceRunID != nil {
		src, ok := v.GetRecord(*b.SourceRunID)
		if !ok || src.Kind != Run || src.Body.(RunBody).SnapshotID != b.SnapshotID {
			return ErrMissingDependency
		}
	}
	conditions := r.Body.(SnapshotBody).Environment.Conditions
	for k, o := range b.Observations {
		expected, ok := conditions[k]
		if !ok {
			return ErrInvalidInput
		}
		if o.Status == "passed" && !reflect.DeepEqual(*o.Value, expected) || o.Status == "failed" && b.Outcome == "succeeded" {
			return ErrConditionMismatch
		}
	}
	return nil
}
