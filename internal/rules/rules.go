package rules

import (
	"regexp"
	"strconv"
	"strings"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
)

var (
	flairPattern         = regexp.MustCompile(`Trades: (\d+)`)
	flairTemplatePattern = regexp.MustCompile(`Trades: ((\d+)-(\d+))`)
)

// IsConfirmingTrade checks if a comment body is confirming a trade.
func IsConfirmingTrade(commentBody string) bool {
	return strings.Contains(strings.ToLower(commentBody), "confirmed")
}

// BuildConfirmationKey builds the idempotency key used for paired flair increments.
func BuildConfirmationKey(parentCommentID, confirmer string) string {
	return strings.ToLower(parentCommentID + ":" + confirmer)
}

// ParseTradeCount extracts tracked trade count from flair text.
// Returns (0, true) for empty flair, (n, true) for tracked, (0, false) for untracked custom flair.
func ParseTradeCount(flairText *string) (count int, tracked bool) {
	if flairText == nil || *flairText == "" {
		return 0, true
	}
	match := flairPattern.FindStringSubmatch(*flairText)
	if match == nil {
		return 0, false
	}
	n, err := strconv.Atoi(match[1])
	if err != nil {
		return 0, false
	}
	return n, true
}

// ParseFlairRange extracts the min-max trade range from a flair template text.
func ParseFlairRange(text string) (min, max int, ok bool) {
	match := flairTemplatePattern.FindStringSubmatch(text)
	if match == nil {
		return 0, 0, false
	}
	min, err1 := strconv.Atoi(match[2])
	max, err2 := strconv.Atoi(match[3])
	if err1 != nil || err2 != nil {
		return 0, 0, false
	}
	return min, max, true
}

// FormatFlairFromTemplate replaces the tracked trade range with the exact count.
func FormatFlairFromTemplate(flairTemplate string, count int) string {
	loc := flairTemplatePattern.FindStringSubmatchIndex(flairTemplate)
	if loc == nil {
		return flairTemplate
	}
	// group 1 is the "min-max" span
	start, end := loc[2], loc[3]
	return flairTemplate[:start] + strconv.Itoa(count) + flairTemplate[end:]
}

// ShouldIncludeComment decides whether a polled comment should be processed.
func ShouldIncludeComment(submissionID, currentSubmissionID string, isRoot bool, bodyLower string) bool {
	if isRoot {
		return submissionID != currentSubmissionID
	}
	return strings.Contains(bodyLower, "confirmed") || strings.Contains(bodyLower, "approved")
}

// IsPossibleWatermarkGap detects when scanning reached the listing limit without finding known IDs.
func IsPossibleWatermarkGap(hadInitialWatermark, foundSeen, listingExhausted bool, scannedCount, gapThreshold int) bool {
	return hadInitialWatermark && !foundSeen && listingExhausted && scannedCount >= gapThreshold
}

// EvaluateConfirmation validates a confirmation comment given pre-fetched context.
func EvaluateConfirmation(comment models.CommentData, ctx models.ConfirmationContext) models.ValidationResult {
	if comment.IsRoot {
		return models.ValidationResult{Valid: false}
	}
	if !ctx.ParentExists || ctx.ParentIsBanned {
		return models.ValidationResult{Valid: false}
	}
	if !ctx.ParentIsProcessable {
		return models.ValidationResult{Valid: false}
	}
	if ctx.ParentAuthorName == comment.AuthorName {
		return models.ValidationResult{Valid: false}
	}

	commentBody := strings.ToLower(comment.Body)

	// Mod approval path: reply to a confirmation (non-root parent).
	if !ctx.ParentIsRoot {
		if strings.Contains(commentBody, "approved") && ctx.IsModerator {
			if ctx.GrandparentExists && ctx.GrandparentIsRoot {
				return models.ValidationResult{
					Valid:            true,
					IsModApproval:    true,
					ParentAuthor:     ctx.GrandparentAuthorName,
					Confirmer:        ctx.ParentAuthorName,
					ParentCommentID:  ctx.GrandparentID,
					ReplyToCommentID: ctx.ParentID,
				}
			}
		}
		return models.ValidationResult{Valid: false}
	}

	if !IsConfirmingTrade(commentBody) {
		return models.ValidationResult{Valid: false}
	}

	if ctx.ParentIsSaved {
		return models.ValidationResult{
			Valid:           false,
			Reason:          "already_confirmed",
			ParentAuthor:    ctx.ParentAuthorName,
			ParentCommentID: ctx.ParentID,
		}
	}

	usernameLower := strings.ToLower(comment.AuthorName)
	if !strings.Contains(ctx.ParentBodyLower, usernameLower) &&
		!strings.Contains(ctx.ParentBodyHTMLLower, usernameLower) {
		return models.ValidationResult{
			Valid:               false,
			Reason:              "cant_confirm_username",
			ParentAuthor:        ctx.ParentAuthorName,
			ParentBodyLower:     ctx.ParentBodyLower,
			ParentBodyHTMLLower: ctx.ParentBodyHTMLLower,
			Confirmer:           usernameLower,
		}
	}

	return models.ValidationResult{
		Valid:            true,
		ParentAuthor:     ctx.ParentAuthorName,
		Confirmer:        comment.AuthorName,
		ParentCommentID:  ctx.ParentID,
		ReplyToCommentID: comment.ID,
	}
}

// FindFlairTemplate finds the flair template matching trade count and mod status.
func FindFlairTemplate(templates []models.FlairTemplate, tradeCount int, isModerator bool) *models.FlairTemplate {
	for i := range templates {
		t := &templates[i]
		if t.Min <= tradeCount && tradeCount <= t.Max && t.ModOnly == isModerator {
			return t
		}
	}
	return nil
}
