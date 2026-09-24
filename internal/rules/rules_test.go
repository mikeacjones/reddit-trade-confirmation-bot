package rules_test

import (
	"testing"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/rules"
)

func TestIsConfirmingTrade(t *testing.T) {
	if !rules.IsConfirmingTrade("Confirmed") {
		t.Fatal("expected true")
	}
	if !rules.IsConfirmingTrade("trade CONFIRMED thanks") {
		t.Fatal("expected true")
	}
	if rules.IsConfirmingTrade("approval pending") {
		t.Fatal("expected false")
	}
}

func TestBuildConfirmationKey(t *testing.T) {
	got := rules.BuildConfirmationKey("AbC123", "SomeUser")
	if got != "abc123:someuser" {
		t.Fatalf("got %q", got)
	}
}

func TestParseTradeCount(t *testing.T) {
	if n, ok := rules.ParseTradeCount(nil); !ok || n != 0 {
		t.Fatalf("nil: %d %v", n, ok)
	}
	empty := ""
	if n, ok := rules.ParseTradeCount(&empty); !ok || n != 0 {
		t.Fatalf("empty: %d %v", n, ok)
	}
	tracked := "Trades: 42"
	if n, ok := rules.ParseTradeCount(&tracked); !ok || n != 42 {
		t.Fatalf("tracked: %d %v", n, ok)
	}
	custom := "Trusted Trader"
	if _, ok := rules.ParseTradeCount(&custom); ok {
		t.Fatal("custom flair should be untracked")
	}
}

func TestFormatFlairFromTemplate(t *testing.T) {
	got := rules.FormatFlairFromTemplate("Collector | Trades: 11-50", 27)
	if got != "Collector | Trades: 27" {
		t.Fatalf("got %q", got)
	}
	if rules.FormatFlairFromTemplate("Custom Flair", 27) != "Custom Flair" {
		t.Fatal("expected unchanged")
	}
}

func TestParseFlairRange(t *testing.T) {
	min, max, ok := rules.ParseFlairRange("Collector | Trades: 11-50")
	if !ok || min != 11 || max != 50 {
		t.Fatalf("got %d %d %v", min, max, ok)
	}
	if _, _, ok := rules.ParseFlairRange("Trusted Trader"); ok {
		t.Fatal("expected untracked")
	}
}

func TestShouldIncludeComment(t *testing.T) {
	if !rules.ShouldIncludeComment("prev1", "cur1", true, "looking to trade") {
		t.Fatal("root on previous should include")
	}
	if rules.ShouldIncludeComment("cur1", "cur1", true, "looking to trade") {
		t.Fatal("root on current should exclude")
	}
	if !rules.ShouldIncludeComment("cur1", "cur1", false, "confirmed") {
		t.Fatal("confirmed should include")
	}
	if !rules.ShouldIncludeComment("cur1", "cur1", false, "mod approved this trade") {
		t.Fatal("approved should include")
	}
	if rules.ShouldIncludeComment("cur1", "cur1", false, "thanks for the trade") {
		t.Fatal("irrelevant should exclude")
	}
}

func TestIsPossibleWatermarkGap(t *testing.T) {
	if !rules.IsPossibleWatermarkGap(true, false, true, 1000, 900) {
		t.Fatal("expected gap")
	}
	if rules.IsPossibleWatermarkGap(true, true, true, 1000, 900) {
		t.Fatal("found seen")
	}
	if rules.IsPossibleWatermarkGap(true, false, true, 100, 900) {
		t.Fatal("below threshold")
	}
}

func defaultComment() models.CommentData {
	return models.CommentData{
		ID: "c1", Body: "confirmed", AuthorName: "Buyer",
		CreatedUTC: 1, IsRoot: false, SubmissionID: "s1",
	}
}

func defaultContext() models.ConfirmationContext {
	return models.ConfirmationContext{
		ParentExists: true, ParentIsProcessable: true,
		ParentAuthorName: "Seller", ParentID: "p1", ParentIsRoot: true,
		ParentBodyLower: "trading with u/buyer",
		ParentBodyHTMLLower: `<p>trading with <a href="/u/buyer">u/buyer</a></p>`,
	}
}

func TestEvaluateConfirmation(t *testing.T) {
	c := defaultComment()
	ctx := defaultContext()

	root := c
	root.IsRoot = true
	if rules.EvaluateConfirmation(root, ctx).Valid {
		t.Fatal("root skipped")
	}

	if rules.EvaluateConfirmation(c, models.ConfirmationContext{ParentExists: false}).Valid {
		t.Fatal("no parent")
	}

	banned := ctx
	banned.ParentIsBanned = true
	if rules.EvaluateConfirmation(c, banned).Valid {
		t.Fatal("banned parent")
	}

	self := c
	self.AuthorName = "Seller"
	if rules.EvaluateConfirmation(self, ctx).Valid {
		t.Fatal("self trade")
	}

	saved := ctx
	saved.ParentIsSaved = true
	r := rules.EvaluateConfirmation(c, saved)
	if r.Valid || r.Reason != "already_confirmed" {
		t.Fatalf("already confirmed: %+v", r)
	}

	nouser := ctx
	nouser.ParentBodyLower = "trading with someone"
	nouser.ParentBodyHTMLLower = "<p>trading with someone</p>"
	r = rules.EvaluateConfirmation(c, nouser)
	if r.Valid || r.Reason != "cant_confirm_username" {
		t.Fatalf("cant confirm: %+v", r)
	}

	r = rules.EvaluateConfirmation(c, ctx)
	if !r.Valid || r.ParentAuthor != "Seller" || r.Confirmer != "Buyer" {
		t.Fatalf("valid: %+v", r)
	}

	modCtx := models.ConfirmationContext{
		ParentExists: true, ParentIsProcessable: true,
		ParentAuthorName: "Confirmer", ParentID: "conf1", ParentIsRoot: false,
		IsModerator: true, GrandparentExists: true, GrandparentIsRoot: true,
		GrandparentAuthorName: "OriginalPoster", GrandparentID: "gp1",
	}
	modComment := c
	modComment.Body = "approved"
	r = rules.EvaluateConfirmation(modComment, modCtx)
	if !r.Valid || !r.IsModApproval || r.ParentAuthor != "OriginalPoster" || r.Confirmer != "Confirmer" {
		t.Fatalf("mod approval: %+v", r)
	}
}

func TestFindFlairTemplate(t *testing.T) {
	templates := []models.FlairTemplate{
		{ID: "t1", Template: "Trades: 0-10", Min: 0, Max: 10, ModOnly: false},
		{ID: "t2", Template: "Trades: 11-50", Min: 11, Max: 50, ModOnly: false},
	}
	got := rules.FindFlairTemplate(templates, 5, false)
	if got == nil || got.ID != "t1" {
		t.Fatalf("got %+v", got)
	}
	got = rules.FindFlairTemplate(templates, 25, false)
	if got == nil || got.ID != "t2" {
		t.Fatalf("got %+v", got)
	}
	if rules.FindFlairTemplate(templates, 99, false) != nil {
		t.Fatal("expected nil")
	}
	modOnly := []models.FlairTemplate{{ID: "t1", Min: 0, Max: 10, ModOnly: true}}
	if rules.FindFlairTemplate(modOnly, 5, false) != nil {
		t.Fatal("mod-only for non-mod")
	}
	if rules.FindFlairTemplate(modOnly, 5, true) == nil {
		t.Fatal("mod-only for mod")
	}
}
