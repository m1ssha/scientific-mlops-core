package catalog

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"reflect"

	"github.com/m1ssha/scientific-mlops-core/object"
)

func (v TypedValue) MarshalJSON() ([]byte, error) {
	if v.Type == "null" && v.Value == nil {
		return []byte(`{"type":"null"}`), nil
	}
	return json.Marshal(struct {
		Type  string `json:"type"`
		Value any    `json:"value"`
	}{v.Type, v.Value})
}
func (v *TypedValue) UnmarshalJSON(data []byte) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	var tag string
	if err := json.Unmarshal(fields["type"], &tag); err != nil {
		return err
	}
	if tag == "null" {
		if len(fields) != 1 {
			return fmt.Errorf("invalid null")
		}
		*v = TypedValue{Type: tag}
		return nil
	}
	if len(fields) != 2 || fields["value"] == nil || bytes.Equal(fields["value"], []byte("null")) {
		return fmt.Errorf("invalid value")
	}
	var value any
	switch tag {
	case "integer":
		value = new(int64)
	case "number":
		value = new(float64)
	case "string":
		value = new(string)
	case "boolean":
		value = new(bool)
	default:
		return fmt.Errorf("invalid type")
	}
	if err := json.Unmarshal(fields["value"], value); err != nil {
		return err
	}
	switch x := value.(type) {
	case *int64:
		v.Value = *x
	case *float64:
		if math.IsInf(*x, 0) || math.IsNaN(*x) {
			return fmt.Errorf("nonfinite")
		}
		if *x == 0 {
			*x = 0
		}
		v.Value = *x
	case *string:
		v.Value = *x
	case *bool:
		v.Value = *x
	}
	v.Type = tag
	return nil
}
func (s MaterialSource) MarshalJSON() ([]byte, error) {
	if s.Kind == "object" {
		return json.Marshal(struct {
			Kind   string      `json:"kind"`
			Object *object.Ref `json:"object"`
		}{s.Kind, s.Object})
	}
	return json.Marshal(struct {
		Kind           string      `json:"kind"`
		Locator        string      `json:"locator"`
		ExpectedObject *object.Ref `json:"expected_object"`
	}{s.Kind, s.Locator, s.ExpectedObject})
}
func (s *MaterialSource) UnmarshalJSON(data []byte) error {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(data, &m); err != nil {
		return err
	}
	var kind string
	if err := json.Unmarshal(m["kind"], &kind); err != nil {
		return err
	}
	if kind == "object" {
		if len(m) != 2 || m["object"] == nil {
			return fmt.Errorf("source fields")
		}
		var ref object.Ref
		if err := strictDecode(m["object"], &ref); err != nil {
			return err
		}
		*s = MaterialSource{Kind: kind, Object: &ref}
		return nil
	}
	if kind == "external" {
		if len(m) != 3 || m["locator"] == nil || m["expected_object"] == nil {
			return fmt.Errorf("source fields")
		}
		var loc string
		if err := json.Unmarshal(m["locator"], &loc); err != nil {
			return err
		}
		var ref *object.Ref
		if !bytes.Equal(bytes.TrimSpace(m["expected_object"]), []byte("null")) {
			ref = new(object.Ref)
			if err := strictDecode(m["expected_object"], ref); err != nil {
				return err
			}
		}
		*s = MaterialSource{Kind: kind, Locator: loc, ExpectedObject: ref}
		return nil
	}
	return fmt.Errorf("source kind")
}

var typedType = reflect.TypeOf(TypedValue{})
var sourceType = reflect.TypeOf(MaterialSource{})

func strictShape(data []byte, t reflect.Type) error {
	if t.Kind() == reflect.Pointer {
		if bytes.Equal(bytes.TrimSpace(data), []byte("null")) {
			return nil
		}
		return strictShape(data, t.Elem())
	}
	if bytes.Equal(bytes.TrimSpace(data), []byte("null")) {
		return fmt.Errorf("unexpected null")
	}
	if t == typedType || t == sourceType {
		return nil
	}
	switch t.Kind() {
	case reflect.Struct:
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(data, &fields); err != nil || fields == nil {
			return fmt.Errorf("expected object")
		}
		if len(fields) != t.NumField() {
			return fmt.Errorf("wrong fields")
		}
		for i := 0; i < t.NumField(); i++ {
			f := t.Field(i)
			name := f.Tag.Get("json")
			x, ok := fields[name]
			if !ok {
				return fmt.Errorf("missing field %s", name)
			}
			if err := strictShape(x, f.Type); err != nil {
				return err
			}
		}
	case reflect.Slice:
		var items []json.RawMessage
		if err := json.Unmarshal(data, &items); err != nil || items == nil {
			return fmt.Errorf("expected array")
		}
		for _, v := range items {
			if err := strictShape(v, t.Elem()); err != nil {
				return err
			}
		}
	case reflect.Map:
		var items map[string]json.RawMessage
		if err := json.Unmarshal(data, &items); err != nil || items == nil {
			return fmt.Errorf("expected map")
		}
		for _, v := range items {
			if err := strictShape(v, t.Elem()); err != nil {
				return err
			}
		}
	}
	return nil
}
func strictDecode(data []byte, dst any) error {
	if err := validateJSON(data); err != nil {
		return err
	}
	if err := strictShape(data, reflect.TypeOf(dst).Elem()); err != nil {
		return err
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	return dec.Decode(dst)
}
