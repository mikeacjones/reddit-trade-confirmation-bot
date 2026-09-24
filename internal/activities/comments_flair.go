package activities

import (
	"context"
	"fmt"
	"strings"
	"time"

	enumspb "go.temporal.io/api/enums/v1"
	"go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/temporal"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/config"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/reddit"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/rules"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/templates"
)

const (
	minPollDelay              = 1 * time.Second
	maxPollDelay              = 4 * time.Second
	watermarkGapScanThreshold = 900
)

// Activities holds dependencies for all Temporal activities.
type Activities struct {
	Cfg       config.Config
	Reddit    *reddit.Client
	Templates *templates.Loader
	Temporal  client.Client
}

// New builds an Activities instance.
func New(cfg config.Config, c client.Client) *Activities {
	r := reddit.New(cfg)
	loader := templates.New(func(name string) (string, error) {
		return r.LoadWikiPage("trade-confirmation-bot/" + name)
	})
	return &Activities{Cfg: cfg, Reddit: r, Templates: loader, Temporal: c}
}

// PollNewComments is a long-running activity that polls until actionable comments or a gap.
func (a *Activities) PollNewComments(ctx context.Context, input models.FetchCommentsInput) (models.FetchCommentsResult, error) {
	botName, _, err := a.Reddit.BotUser()
	if err != nil {
		return models.FetchCommentsResult{}, err
	}
	activeIDs := map[string]struct{}{}
	for _, id := range input.ActiveSubmissionIDs {
		activeIDs[id] = struct{}{}
	}
	knownIDs := map[string]struct{}{}
	for _, id := range input.SeenIDs {
		knownIDs[id] = struct{}{}
	}
	hasWatermark := len(knownIDs) > 0
	var newIDs []string
	pollDelay := minPollDelay

	for {
		if err := ctx.Err(); err != nil {
			return models.FetchCommentsResult{}, err
		}

		var comments []models.CommentData
		scannedCount := 0
		hitKnown := false
		stoppedEarly := false
		var batchNewIDs []string

		err := a.Reddit.IterComments(func(cm reddit.Comment, scanned int) bool {
			scannedCount = scanned
			if scanned%100 == 0 {
				activity.RecordHeartbeat(ctx, fmt.Sprintf("Scanned %d comments", scannedCount))
			}
			if _, ok := knownIDs[cm.ID]; ok || cm.Saved {
				hitKnown = true
			} else {
				knownIDs[cm.ID] = struct{}{}
				batchNewIDs = append(batchNewIDs, cm.ID)
				submissionID := cm.SubmissionID()
				if _, active := activeIDs[submissionID]; active {
					if !cm.IsBanned() && reddit.ShouldProcessRedditor(cm.Author, false, false, botName) {
						if rules.ShouldIncludeComment(submissionID, input.CurrentSubmissionID, cm.IsRoot(), strings.ToLower(cm.Body)) {
							comments = append(comments, reddit.SerializeComment(cm))
						}
					}
				}
			}
			if scanned%100 == 0 && hitKnown {
				stoppedEarly = true
				return false
			}
			return true
		})
		if err != nil {
			return models.FetchCommentsResult{}, err
		}

		newIDs = append(batchNewIDs, newIDs...)
		foundSeen := hitKnown || !hasWatermark
		listingExhausted := !stoppedEarly
		possibleGap := rules.IsPossibleWatermarkGap(hasWatermark, foundSeen, listingExhausted, scannedCount, watermarkGapScanThreshold)

		if len(comments) > 0 || possibleGap {
			return models.FetchCommentsResult{
				Comments:     comments,
				ScannedIDs:   newIDs,
				FoundSeen:    foundSeen,
				ScannedCount: scannedCount,
				PossibleGap:  possibleGap,
			}, nil
		}

		pollDelay *= 2
		if pollDelay > maxPollDelay {
			pollDelay = maxPollDelay
		}
		select {
		case <-ctx.Done():
			return models.FetchCommentsResult{}, ctx.Err()
		case <-time.After(pollDelay):
		}
	}
}

// ValidateConfirmation fetches parent/grandparent data and validates.
func (a *Activities) ValidateConfirmation(ctx context.Context, commentData models.CommentData) (models.ValidationResult, error) {
	botName, _, err := a.Reddit.BotUser()
	if err != nil {
		return models.ValidationResult{}, err
	}
	comment, err := a.Reddit.GetComment(commentData.ID)
	if err != nil {
		return models.ValidationResult{}, err
	}
	confCtx := models.ConfirmationContext{ParentExists: false}
	if comment == nil || strings.HasPrefix(comment.ParentID, "t3_") {
		return rules.EvaluateConfirmation(commentData, confCtx), nil
	}

	parentID := strings.TrimPrefix(comment.ParentID, "t1_")
	parent, err := a.Reddit.GetComment(parentID)
	if err != nil {
		return models.ValidationResult{}, err
	}
	confCtx.ParentExists = parent != nil
	if parent != nil {
		confCtx.ParentIsBanned = parent.IsBanned()
		confCtx.ParentIsProcessable = !confCtx.ParentIsBanned &&
			reddit.ShouldProcessRedditor(parent.Author, false, false, botName)
		if confCtx.ParentIsProcessable {
			confCtx.ParentAuthorName = parent.Author
			confCtx.ParentID = parent.ID
			confCtx.ParentIsRoot = parent.IsRoot()
			confCtx.ParentIsSaved = parent.Saved
			confCtx.ParentBodyLower = strings.ToLower(strings.ReplaceAll(parent.Body, "\\", ""))
			confCtx.ParentBodyHTMLLower = strings.ToLower(parent.BodyHTML)
			isMod, err := a.Reddit.IsModerator(commentData.AuthorName)
			if err != nil {
				return models.ValidationResult{}, err
			}
			confCtx.IsModerator = isMod

			if !parent.IsRoot() && strings.HasPrefix(parent.ParentID, "t1_") {
				gpID := strings.TrimPrefix(parent.ParentID, "t1_")
				gp, err := a.Reddit.GetComment(gpID)
				if err != nil {
					return models.ValidationResult{}, err
				}
				if gp != nil {
					confCtx.GrandparentExists = true
					confCtx.GrandparentIsRoot = gp.IsRoot()
					confCtx.GrandparentAuthorName = gp.Author
					confCtx.GrandparentID = gp.ID
				}
			}
		}
	}
	return rules.EvaluateConfirmation(commentData, confCtx), nil
}

// MarkCommentSaved marks a comment as saved/processed.
func (a *Activities) MarkCommentSaved(ctx context.Context, commentID string) error {
	return a.Reddit.SaveComment(commentID)
}

// ReplyToComment replies using a template.
func (a *Activities) ReplyToComment(ctx context.Context, input models.ReplyToCommentInput) (string, error) {
	var text string
	var err error
	if input.FormatArgs != nil {
		text, err = a.Templates.Format(input.TemplateName, input.FormatArgs)
	} else {
		text, err = a.Templates.Load(input.TemplateName)
	}
	if err != nil {
		return "", err
	}
	id, permalink, err := a.Reddit.ReplyToComment(input.CommentID, text)
	if err != nil {
		return "", err
	}
	activity.GetLogger(ctx).Info("Replied to comment", "url", "https://reddit.com"+permalink)
	return id, nil
}

// GetUserFlair returns a user's current flair information.
func (a *Activities) GetUserFlair(ctx context.Context, username string) (models.UserFlairResult, error) {
	flairText, err := a.Reddit.GetUserFlair(username)
	if err != nil {
		return models.UserFlairResult{}, err
	}
	count, tracked := rules.ParseTradeCount(flairText)
	var tradeCount *int
	if tracked {
		c := count
		tradeCount = &c
	}
	return models.UserFlairResult{
		FlairText:      flairText,
		TradeCount:     tradeCount,
		IsTradeTracked: tracked,
	}, nil
}

// SetUserFlair sets a user's flair to an exact trade count.
func (a *Activities) SetUserFlair(ctx context.Context, input models.SetUserFlairInput) (models.FlairUpdateResult, error) {
	tmpls, err := a.Reddit.FlairTemplates()
	if err != nil {
		return models.FlairUpdateResult{}, err
	}
	isMod, err := a.Reddit.IsModerator(input.Username)
	if err != nil {
		return models.FlairUpdateResult{}, err
	}
	tmpl := rules.FindFlairTemplate(tmpls, input.NewCount, isMod)
	var newFlair *string
	if tmpl == nil {
		activity.GetLogger(ctx).Warn("No flair template found", "trades", input.NewCount)
	} else {
		text := rules.FormatFlairFromTemplate(tmpl.Template, input.NewCount)
		if err := a.Reddit.SetUserFlair(input.Username, text, tmpl.ID); err != nil {
			return models.FlairUpdateResult{}, err
		}
		newFlair = &text
	}
	old := ""
	if input.OldFlair != nil {
		old = *input.OldFlair
	}
	nf := ""
	if newFlair != nil {
		nf = *newFlair
	}
	activity.GetLogger(ctx).Info("Flair set", "user", input.Username, "old", old, "new", nf)
	return models.FlairUpdateResult{NewFlair: newFlair}, nil
}

// RequestFlairIncrement routes increment requests through the flair coordinator workflow.
func (a *Activities) RequestFlairIncrement(ctx context.Context, request models.FlairIncrementRequest) (models.FlairIncrementResult, error) {
	if request.Delta == 0 {
		request.Delta = 1
	}
	wfID := "flair-coordinator-" + a.Cfg.SubredditName
	startOp := a.Temporal.NewWithStartWorkflowOperation(client.StartWorkflowOptions{
		ID:                       wfID,
		TaskQueue:                a.Cfg.TaskQueue,
		WorkflowIDConflictPolicy: enumspb.WORKFLOW_ID_CONFLICT_POLICY_USE_EXISTING,
		TypedSearchAttributes: temporal.NewSearchAttributes(
			models.RedditSubreddit.ValueSet(a.Cfg.SubredditName),
		),
		StaticSummary: "r/" + a.Cfg.SubredditName,
	}, "FlairCoordinatorWorkflow")

	handle, err := a.Temporal.UpdateWithStartWorkflow(ctx, client.UpdateWithStartWorkflowOptions{
		UpdateOptions: client.UpdateWorkflowOptions{
			WorkflowID:   wfID,
			UpdateName:   "apply_increment",
			UpdateID:     request.RequestID,
			Args:         []any{request},
			WaitForStage: client.WorkflowUpdateStageCompleted,
		},
		StartWorkflowOperation: startOp,
	})
	if err != nil {
		return models.FlairIncrementResult{}, err
	}
	var result models.FlairIncrementResult
	if err := handle.Get(ctx, &result); err != nil {
		return models.FlairIncrementResult{}, err
	}
	return result, nil
}
