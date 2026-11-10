package catalog

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"os"
	"reflect"
	"strings"
	"testing"
)

type diffStore struct {
	v             View
	reads, writes int
}

func (s *diffStore) Read(context.Context) (View, error) { s.reads++; return s.v, nil }
func (s *diffStore) Update(context.Context, func(Tx) error) error {
	s.writes++
	return errors.New("unexpected write")
}
func TestDiffContractExamples(t *testing.T) {
	raw, err := os.ReadFile("../docs/contracts/v1/catalog-example.json")
	if err != nil {
		t.Fatal(err)
	}
	g, err := decodeGeneration(raw)
	if err != nil {
		t.Fatal(err)
	}
	raw, err = os.ReadFile("../docs/contracts/v1/diff-examples.json")
	if err != nil {
		t.Fatal(err)
	}
	var examples []struct {
		Kind    RecordKind `json:"kind"`
		LeftID  ID         `json:"left_id"`
		RightID ID         `json:"right_id"`
	}
	if err = json.Unmarshal(raw, &examples); err != nil {
		t.Fatal(err)
	}
	var expected []json.RawMessage
	if err = json.Unmarshal(raw, &expected); err != nil {
		t.Fatal(err)
	}
	store := &diffStore{v: snapshot{g}}
	svc := NewService(store, nil)
	for i, ex := range examples {
		d, e := svc.Compare(context.Background(), ex.Kind, ex.LeftID, ex.RightID)
		if e != nil {
			t.Fatal(e)
		}
		got, _ := json.Marshal(d)
		var a, b any
		json.Unmarshal(got, &a)
		json.Unmarshal(expected[i], &b)
		if !reflect.DeepEqual(a, b) {
			t.Fatalf("example %d: %s", i, got)
		}
		rev, e := svc.Compare(context.Background(), ex.Kind, ex.RightID, ex.LeftID)
		if e != nil {
			t.Fatal(e)
		}
		if len(rev.Changes) != len(d.Changes) {
			t.Fatal(rev)
		}
		for j, c := range d.Changes {
			rr := rev.Changes[j]
			kind := c.Kind
			if kind == "added" {
				kind = "removed"
			} else if kind == "removed" {
				kind = "added"
			}
			if rr.Path != c.Path || rr.Kind != kind || !reflect.DeepEqual(rr.Left, c.Right) || !reflect.DeepEqual(rr.Right, c.Left) {
				t.Fatalf("reverse %#v %#v", c, rr)
			}
		}
	}
	if store.reads != 4 || store.writes != 0 || store.v.Sequence() != 6 {
		t.Fatal(store)
	}
	if _, e := svc.Compare(context.Background(), Dataset, examples[0].LeftID, examples[1].LeftID); !errors.Is(e, ErrNotFound) {
		t.Fatal(e)
	}
	if _, e := svc.Compare(context.Background(), Dataset, examples[0].LeftID, ID("ffffffffffffffffffffffffffffffff")); !errors.Is(e, ErrNotFound) {
		t.Fatal(e)
	}
}
func TestDiffMetricAndTypedValues(t *testing.T) {
	tests := []struct {
		name     string
		l, r     Metric
		profiles bool
		kind     string
		delta    float64
	}{
		{"delta", Metric{"m", "s", 2}, Metric{"m", "s", 5}, true, "changed", 3},
		{"profile", Metric{"m", "s", 2}, Metric{"m", "s", 2}, false, "incomparable", 0},
		{"definition", Metric{"m", "s", 2}, Metric{"n", "s", 2}, true, "incomparable", 0},
		{"unit", Metric{"m", "s", 2}, Metric{"m", "ms", 2}, true, "incomparable", 0},
		{"overflow", Metric{"m", "s", -math.MaxFloat64}, Metric{"m", "s", math.MaxFloat64}, true, "incomparable", 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var changes []Change
			compareMaps(&changes, "/metrics", map[string]any{"m": tt.l}, map[string]any{"m": tt.r}, tt.profiles)
			if len(changes) != 1 || changes[0].Kind != tt.kind {
				t.Fatal(changes)
			}
			if tt.kind == "changed" && (changes[0].Delta == nil || *changes[0].Delta != tt.delta) {
				t.Fatal(changes)
			}
			if tt.kind == "incomparable" && changes[0].Delta != nil {
				t.Fatal(changes)
			}
		})
	}
	l := map[string]any{"a/b~c": TypedValue{Type: "integer", Value: int64(1)}, "null": TypedValue{Type: "null"}, "gone": TypedValue{Type: "string", Value: ""}}
	r := map[string]any{"a/b~c": TypedValue{Type: "number", Value: float64(1)}, "null": TypedValue{Type: "string", Value: ""}, "new": TypedValue{Type: "null"}}
	var changes []Change
	compareMaps(&changes, "/parameters", l, r, true)
	seen := map[string]string{}
	for _, c := range changes {
		seen[c.Path] = c.Kind
	}
	want := map[string]string{"/parameters/a~1b~0c": "changed", "/parameters/null": "changed", "/parameters/gone": "removed", "/parameters/new": "added"}
	if !reflect.DeepEqual(seen, want) {
		t.Fatal(seen)
	}
}

func TestCompareRunsProfilesAndDirections(t *testing.T) {
	raw, err := os.ReadFile("../docs/contracts/v1/catalog-example.json")
	if err != nil {
		t.Fatal(err)
	}
	g, err := decodeGeneration(raw)
	if err != nil {
		t.Fatal(err)
	}
	left := ID("00000000000000000000000000000003")
	right := ID("00000000000000000000000000000004")
	snapID := ID("00000000000000000000000000000002")
	lr := g.Records[left]
	lb := lr.Body.(RunBody)
	lb.Metrics = map[string]Metric{"accuracy": {Definition: "accuracy", Unit: "ratio", Value: 0.25}}
	lr.Body = lb
	g.Records[left] = lr
	rr := g.Records[right]
	rb := rr.Body.(RunBody)
	rb.Metrics = map[string]Metric{"accuracy": {Definition: "accuracy", Unit: "ratio", Value: 0.75}, "new": {Definition: "new", Unit: "ratio", Value: 1}}
	rr.Body = rb
	g.Records[right] = rr
	store := &diffStore{v: snapshot{g}}
	svc := NewService(store, nil)
	find := func(d Diff, path string) Change {
		for _, c := range d.Changes {
			if c.Path == path {
				return c
			}
		}
		t.Fatalf("missing %s: %#v", path, d.Changes)
		return Change{}
	}
	d, err := svc.CompareRuns(context.Background(), left, right)
	if err != nil {
		t.Fatal(err)
	}
	c := find(d, "/metrics/accuracy")
	if c.Kind != "changed" || c.Delta == nil || *c.Delta != 0.5 {
		t.Fatal(c)
	}
	if find(d, "/metrics/new").Kind != "added" {
		t.Fatal(d)
	}
	d, err = svc.CompareRuns(context.Background(), right, left)
	if err != nil {
		t.Fatal(err)
	}
	c = find(d, "/metrics/accuracy")
	if c.Delta == nil || *c.Delta != -0.5 || find(d, "/metrics/new").Kind != "removed" {
		t.Fatal(d)
	}
	otherID := ID("00000000000000000000000000000007")
	sr := g.Records[snapID]
	sb := sr.Body.(SnapshotBody)
	sb.ProfileID = "other"
	sr.ID = otherID
	sr.Body = sb
	g.Records[otherID] = sr
	rb.SnapshotID = otherID
	rr.Body = rb
	g.Records[right] = rr
	store.v = snapshot{g}
	d, err = svc.CompareRuns(context.Background(), left, right)
	if err != nil {
		t.Fatal(err)
	}
	c = find(d, "/metrics/accuracy")
	if c.Kind != "incomparable" || c.Delta != nil {
		t.Fatal(c)
	}
}

func TestCompareEnvironmentConditionFields(t *testing.T) {
	raw, err := os.ReadFile("../docs/contracts/v1/catalog-example.json")
	if err != nil {
		t.Fatal(err)
	}
	g, err := decodeGeneration(raw)
	if err != nil {
		t.Fatal(err)
	}
	leftSnapshot := ID("00000000000000000000000000000002")
	rightSnapshot := ID("00000000000000000000000000000007")
	leftRecord := g.Records[leftSnapshot]
	leftBody := leftRecord.Body.(SnapshotBody)
	leftBody.Environment.Conditions = map[string]TypedValue{
		"a/b~c":   {Type: "null"},
		"removed": {Type: "boolean", Value: true},
	}
	leftRecord.Body = leftBody
	g.Records[leftSnapshot] = leftRecord
	rightRecord := leftRecord
	rightRecord.ID = rightSnapshot
	rightBody := leftBody
	rightBody.Environment.Conditions = map[string]TypedValue{
		"a/b~c": {Type: "string", Value: ""},
		"added": {Type: "null"},
	}
	rightRecord.Body = rightBody
	g.Records[rightSnapshot] = rightRecord
	leftRun := ID("00000000000000000000000000000003")
	rightRun := ID("00000000000000000000000000000004")
	runRecord := g.Records[rightRun]
	runBody := runRecord.Body.(RunBody)
	runBody.SnapshotID = rightSnapshot
	runRecord.Body = runBody
	g.Records[rightRun] = runRecord
	svc := NewService(&diffStore{v: snapshot{g}}, nil)
	for _, tc := range []struct {
		kind        RecordKind
		left, right ID
		prefix      string
	}{
		{Snapshot, leftSnapshot, rightSnapshot, "/environment/conditions"},
		{Run, leftRun, rightRun, "/conditions/environment/conditions"},
	} {
		for _, reverse := range []bool{false, true} {
			l, r := tc.left, tc.right
			if reverse {
				l, r = r, l
			}
			d, e := svc.Compare(context.Background(), tc.kind, l, r)
			if e != nil {
				t.Fatal(e)
			}
			var got []Change
			for _, c := range d.Changes {
				if strings.HasPrefix(c.Path, tc.prefix+"/") {
					got = append(got, c)
				}
			}
			want := []Change{
				{Path: tc.prefix + "/added", Kind: "added", Right: map[string]any{"type": "null"}},
				{Path: tc.prefix + "/a~1b~0c/type", Kind: "changed", Left: "null", Right: "string"},
				{Path: tc.prefix + "/a~1b~0c/value", Kind: "added", Right: ""},
				{Path: tc.prefix + "/removed", Kind: "removed", Left: map[string]any{"type": "boolean", "value": true}},
			}
			if reverse {
				for i := range want {
					c := &want[i]
					c.Left, c.Right = c.Right, c.Left
					if c.Kind == "added" {
						c.Kind = "removed"
					} else if c.Kind == "removed" {
						c.Kind = "added"
					}
				}
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("%s reverse=%v: got %#v want %#v", tc.kind, reverse, got, want)
			}
		}
	}
}
