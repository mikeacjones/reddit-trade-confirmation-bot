package services

import (
	"fmt"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/rules"
)

// BuildInvalidReply builds the reply payload for a rejected confirmation.
func BuildInvalidReply(comment models.CommentData, validation models.ValidationResult) *models.ReplyToCommentInput {
	if validation.Reason == "" {
		return nil
	}
	return &models.ReplyToCommentInput{
		CommentID:    comment.ID,
		TemplateName: validation.Reason,
		FormatArgs: map[string]any{
			"id":                comment.ID,
			"body":              comment.Body,
			"author_name":       comment.AuthorName,
			"created_utc":       comment.CreatedUTC,
			"is_root":           comment.IsRoot,
			"submission_id":     comment.SubmissionID,
			"parent_author":     validation.ParentAuthor,
			"parent_comment_id": validation.ParentCommentID,
		},
	}
}

// BuildFlairIncrementRequests builds paired flair increment requests for a confirmed trade.
func BuildFlairIncrementRequests(validation models.ValidationResult) (models.FlairIncrementRequest, models.FlairIncrementRequest, error) {
	if validation.ParentAuthor == "" || validation.Confirmer == "" {
		return models.FlairIncrementRequest{}, models.FlairIncrementRequest{},
			fmt.Errorf("confirmed validation must include parent_author and confirmer")
	}
	key := rules.BuildConfirmationKey(validation.ParentCommentID, validation.Confirmer)
	return models.FlairIncrementRequest{
			Username:  validation.ParentAuthor,
			RequestID: key + ":parent",
			Delta:     1,
		}, models.FlairIncrementRequest{
			Username:  validation.Confirmer,
			RequestID: key + ":confirmer",
			Delta:     1,
		}, nil
}

// BuildConfirmationReply builds the reply payload for a successful confirmation.
func BuildConfirmationReply(
	fallbackCommentID string,
	validation models.ValidationResult,
	parentResult, confirmerResult models.FlairIncrementResult,
) models.ReplyToCommentInput {
	replyCommentID := validation.ReplyToCommentID
	if replyCommentID == "" {
		replyCommentID = fallbackCommentID
	}
	return models.ReplyToCommentInput{
		CommentID:    replyCommentID,
		TemplateName: "trade_confirmation",
		FormatArgs: map[string]any{
			"comment_id":        replyCommentID,
			"confirmer":         validation.Confirmer,
			"parent_author":     validation.ParentAuthor,
			"old_comment_flair": stringOr(confirmerResult.OldFlair, "unknown"),
			"new_comment_flair": stringOr(confirmerResult.NewFlair, "unknown"),
			"old_parent_flair":  stringOr(parentResult.OldFlair, "unknown"),
			"new_parent_flair":  stringOr(parentResult.NewFlair, "unknown"),
		},
	}
}

// BuildConfirmedResult builds the workflow result for a successful confirmation.
func BuildConfirmedResult(
	commentID string,
	validation models.ValidationResult,
	parentResult, confirmerResult models.FlairIncrementResult,
) map[string]any {
	return map[string]any{
		"status":              "confirmed",
		"comment_id":          commentID,
		"parent_author":       validation.ParentAuthor,
		"confirmer":           validation.Confirmer,
		"parent_new_flair":    parentResult.NewFlair,
		"confirmer_new_flair": confirmerResult.NewFlair,
	}
}

func stringOr(p *string, fallback string) string {
	if p == nil || *p == "" {
		return fallback
	}
	return *p
}
