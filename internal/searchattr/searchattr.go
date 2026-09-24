package searchattr

import (
	"context"
	"fmt"
	"log/slog"
	"strings"

	"go.temporal.io/api/enums/v1"
	"go.temporal.io/api/operatorservice/v1"
	"go.temporal.io/api/serviceerror"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/temporal"
)

var (
	RedditSubreddit          = temporal.NewSearchAttributeKeyKeyword("RedditSubreddit")
	RedditCommentID          = temporal.NewSearchAttributeKeyKeyword("RedditCommentId")
	RedditSubmissionID       = temporal.NewSearchAttributeKeyKeyword("RedditSubmissionId")
	RedditConfirmationStatus = temporal.NewSearchAttributeKeyKeyword("RedditConfirmationStatus")
)

// EnsureSearchAttributes creates required custom search attributes if missing.
func EnsureSearchAttributes(ctx context.Context, c client.Client, namespace string) error {
	keys := []struct {
		name string
		typ  enums.IndexedValueType
	}{
		{RedditSubreddit.GetName(), enums.INDEXED_VALUE_TYPE_KEYWORD},
		{RedditCommentID.GetName(), enums.INDEXED_VALUE_TYPE_KEYWORD},
		{RedditSubmissionID.GetName(), enums.INDEXED_VALUE_TYPE_KEYWORD},
		{RedditConfirmationStatus.GetName(), enums.INDEXED_VALUE_TYPE_KEYWORD},
	}

	resp, err := c.OperatorService().ListSearchAttributes(ctx, &operatorservice.ListSearchAttributesRequest{
		Namespace: namespace,
	})
	if err != nil {
		return fmt.Errorf("list search attributes: %w", err)
	}

	missing := map[string]enums.IndexedValueType{}
	var mismatched []string
	for _, key := range keys {
		actual, ok := resp.CustomAttributes[key.name]
		if !ok {
			actual, ok = resp.SystemAttributes[key.name]
			if !ok {
				missing[key.name] = key.typ
				continue
			}
		}
		if actual != key.typ {
			mismatched = append(mismatched, fmt.Sprintf("%s exists as %v, expected %v", key.name, actual, key.typ))
		}
	}
	if len(mismatched) > 0 {
		return fmt.Errorf("Temporal search attribute type mismatch: %s", strings.Join(mismatched, "; "))
	}
	if len(missing) == 0 {
		slog.Info("Temporal search attributes already registered")
		return nil
	}

	_, err = c.OperatorService().AddSearchAttributes(ctx, &operatorservice.AddSearchAttributesRequest{
		Namespace:        namespace,
		SearchAttributes: missing,
	})
	if err != nil {
		if _, ok := err.(*serviceerror.AlreadyExists); ok {
			slog.Info("Temporal search attributes already registered")
			return nil
		}
		return fmt.Errorf("add search attributes: %w", err)
	}

	names := make([]string, 0, len(missing))
	for n := range missing {
		names = append(names, n)
	}
	slog.Info("Registered Temporal search attributes", "attrs", strings.Join(names, ", "))
	return nil
}
