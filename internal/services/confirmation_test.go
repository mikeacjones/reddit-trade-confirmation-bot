package services_test

import (
	"testing"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/services"
)

func strPtr(s string) *string { return &s }

func TestBuildInvalidReply(t *testing.T) {
	comment := models.CommentData{
		ID: "c1", Body: "confirmed", AuthorName: "Confirmer",
		CreatedUTC: 1, SubmissionID: "s1",
	}
	validation := models.ValidationResult{
		Valid: false, Reason: "cant_confirm_username",
		ParentAuthor: "Seller", ParentCommentID: "parent1",
	}
	reply := services.BuildInvalidReply(comment, validation)
	if reply == nil {
		t.Fatal("expected reply")
	}
	if reply.CommentID != "c1" || reply.TemplateName != "cant_confirm_username" {
		t.Fatalf("%+v", reply)
	}
	if reply.FormatArgs["parent_author"] != "Seller" {
		t.Fatal("missing parent_author")
	}
	if services.BuildInvalidReply(comment, models.ValidationResult{Valid: false}) != nil {
		t.Fatal("skip should be nil")
	}
}

func TestBuildFlairIncrementRequests(t *testing.T) {
	validation := models.ValidationResult{
		Valid: true, ParentAuthor: "Seller", Confirmer: "Buyer", ParentCommentID: "AbC123",
	}
	parent, confirmer, err := services.BuildFlairIncrementRequests(validation)
	if err != nil {
		t.Fatal(err)
	}
	if parent.RequestID != "abc123:buyer:parent" || confirmer.RequestID != "abc123:buyer:confirmer" {
		t.Fatalf("%q %q", parent.RequestID, confirmer.RequestID)
	}
}

func TestBuildConfirmationReply(t *testing.T) {
	validation := models.ValidationResult{
		Valid: true, ParentAuthor: "Seller", Confirmer: "Buyer", ReplyToCommentID: "reply123",
	}
	reply := services.BuildConfirmationReply("fallback", validation,
		models.FlairIncrementResult{OldFlair: strPtr("Trades: 2"), NewFlair: strPtr("Trades: 3")},
		models.FlairIncrementResult{OldFlair: strPtr("Trades: 4"), NewFlair: strPtr("Trades: 5")},
	)
	if reply.CommentID != "reply123" || reply.TemplateName != "trade_confirmation" {
		t.Fatalf("%+v", reply)
	}
	if reply.FormatArgs["new_parent_flair"] != "Trades: 3" {
		t.Fatal(reply.FormatArgs)
	}
}

func TestBuildConfirmedResult(t *testing.T) {
	result := services.BuildConfirmedResult("comment1",
		models.ValidationResult{Valid: true, ParentAuthor: "Seller", Confirmer: "Buyer"},
		models.FlairIncrementResult{NewFlair: strPtr("Trades: 3")},
		models.FlairIncrementResult{NewFlair: strPtr("Trades: 5")},
	)
	if result["status"] != "confirmed" || result["comment_id"] != "comment1" {
		t.Fatalf("%+v", result)
	}
}
