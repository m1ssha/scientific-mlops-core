package catalog

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"unicode/utf8"
)

// validateJSON checks the token stream before decoding so duplicate keys cannot
// be hidden by encoding/json's last-key-wins behavior.
func validateJSON(data []byte) error {
	if !utf8.Valid(data) {
		return fmt.Errorf("invalid UTF-8")
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	var value func() error
	value = func() error {
		t, e := d.Token()
		if e != nil {
			return e
		}
		delim, ok := t.(json.Delim)
		if !ok {
			return nil
		}
		switch delim {
		case '{':
			seen := map[string]bool{}
			for d.More() {
				key, e := d.Token()
				if e != nil {
					return e
				}
				s, ok := key.(string)
				if !ok || seen[s] {
					return fmt.Errorf("duplicate or invalid key")
				}
				seen[s] = true
				if e = value(); e != nil {
					return e
				}
			}
			end, e := d.Token()
			if e != nil || end != json.Delim('}') {
				return fmt.Errorf("invalid object")
			}
		case '[':
			for d.More() {
				if e = value(); e != nil {
					return e
				}
			}
			end, e := d.Token()
			if e != nil || end != json.Delim(']') {
				return fmt.Errorf("invalid array")
			}
		default:
			return fmt.Errorf("unexpected delimiter")
		}
		return nil
	}
	if e := value(); e != nil {
		return e
	}
	if _, e := d.Token(); e != io.EOF {
		return fmt.Errorf("trailing data")
	}
	return nil
}
func decodeExact(data []byte, dst any, fields ...string) error {
	if e := validateJSON(data); e != nil {
		return e
	}
	var raw map[string]json.RawMessage
	if e := json.Unmarshal(data, &raw); e != nil {
		return e
	}
	if len(raw) != len(fields) {
		return fmt.Errorf("wrong fields")
	}
	for _, f := range fields {
		v, ok := raw[f]
		if !ok {
			return fmt.Errorf("missing %s", f)
		}
		if bytes.Equal(bytes.TrimSpace(v), []byte("null")) && f != "predecessor_id" && f != "record_kind" && f != "record_id" && f != "reference" && f != "ref_update" {
			return fmt.Errorf("null %s", f)
		}
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	return dec.Decode(dst)
}

// DecodeCommand parses the closed v1 command representation.
func DecodeCommand(data []byte) (Command, error) {
	var envelope struct {
		SchemaVersion int             `json:"schema_version"`
		RepositoryID  ID              `json:"repository_id"`
		OperationID   ID              `json:"operation_id"`
		Type          string          `json:"type"`
		Body          json.RawMessage `json:"body"`
		RefUpdate     json.RawMessage `json:"ref_update"`
	}
	if e := decodeExact(data, &envelope, "schema_version", "repository_id", "operation_id", "type", "body", "ref_update"); e != nil {
		return Command{}, fmt.Errorf("%w: %v", ErrInvalidInput, e)
	}
	if envelope.SchemaVersion != 1 {
		return Command{}, ErrUnsupportedSchema
	}
	c := Command{SchemaVersion: envelope.SchemaVersion, RepositoryID: envelope.RepositoryID, OperationID: envelope.OperationID, Type: envelope.Type}
	if !bytes.Equal(bytes.TrimSpace(envelope.RefUpdate), []byte("null")) {
		var u RefUpdate
		if e := decodeExact(envelope.RefUpdate, &u, "name", "expected_revision"); e != nil {
			return c, fmt.Errorf("%w: %v", ErrInvalidInput, e)
		}
		c.RefUpdate = &u
	}
	switch envelope.Type {
	case "publish_dataset":
		var b struct {
			PredecessorID json.RawMessage   `json:"predecessor_id"`
			Files         []json.RawMessage `json:"files"`
		}
		if e := decodeExact(envelope.Body, &b, "predecessor_id", "files"); e != nil || b.Files == nil {
			return c, ErrInvalidInput
		}
		body := DatasetBody{Files: make([]DatasetFile, 0, len(b.Files))}
		if !bytes.Equal(bytes.TrimSpace(b.PredecessorID), []byte("null")) {
			var id ID
			if e := json.Unmarshal(b.PredecessorID, &id); e != nil {
				return c, ErrInvalidInput
			}
			body.PredecessorID = &id
		}
		for _, f := range b.Files {
			var raw struct {
				Path   string          `json:"path"`
				Object json.RawMessage `json:"object"`
			}
			if e := decodeExact(f, &raw, "path", "object"); e != nil {
				return c, ErrInvalidInput
			}
			var item DatasetFile
			item.Path = raw.Path
			if e := decodeExact(raw.Object, &item.Object, "algorithm", "digest", "size"); e != nil {
				return c, ErrInvalidInput
			}
			body.Files = append(body.Files, item)
		}
		c.Body = body
	case "set_reference":
		var b SetReferenceBody
		if e := decodeExact(envelope.Body, &b, "name", "target_kind", "target_id", "expected_revision"); e != nil {
			return c, ErrInvalidInput
		}
		c.Body = b
	default:
		return c, ErrInvalidInput
	}
	return normalize(c, c.Type)
}
