package catalog

import (
	"fmt"
	"sort"
	"strings"
	"unicode/utf8"
)

// ValidID reports whether id is a valid v1 opaque identifier.
func ValidID(id ID) bool { return validID(id) }
func validID(id ID) bool {
	if len(id) != 32 {
		return false
	}
	for _, c := range id {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}
func validName(s string) bool { return s != "" && utf8.ValidString(s) && !strings.ContainsRune(s, 0) }
func validPath(s string) bool {
	if !validName(s) || strings.HasPrefix(s, "/") || strings.ContainsRune(s, '\\') {
		return false
	}
	for _, p := range strings.Split(s, "/") {
		if p == "" || p == "." || p == ".." {
			return false
		}
	}
	return true
}
func normalizeDataset(body DatasetBody) (DatasetBody, error) {
	if body.Files == nil || body.PredecessorID != nil && !validID(*body.PredecessorID) {
		return DatasetBody{}, fmt.Errorf("%w: invalid dataset", ErrInvalidInput)
	}
	body.Files = append([]DatasetFile{}, body.Files...)
	sort.Slice(body.Files, func(i, j int) bool { return body.Files[i].Path < body.Files[j].Path })
	seen := make(map[string]bool, len(body.Files))
	for _, f := range body.Files {
		if !validPath(f.Path) || f.Object.Validate() != nil {
			return DatasetBody{}, fmt.Errorf("%w: invalid dataset file", ErrInvalidInput)
		}
		if seen[f.Path] {
			return DatasetBody{}, fmt.Errorf("%w: colliding paths", ErrInvalidInput)
		}
		seen[f.Path] = true
		for i, ch := range f.Path {
			if ch == '/' && seen[f.Path[:i]] {
				return DatasetBody{}, fmt.Errorf("%w: colliding paths", ErrInvalidInput)
			}
		}
	}

	if body.PredecessorID != nil {
		id := *body.PredecessorID
		body.PredecessorID = &id
	}
	return body, nil
}
