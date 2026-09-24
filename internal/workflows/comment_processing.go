package workflows

import (
	"errors"
	"fmt"
	"time"

	enumspb "go.temporal.io/api/enums/v1"
	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/rules"
)

// Set at worker startup from environment (one worker process per subreddit).
var (
	SubredditName string
	TaskQueue     string
)

type commentPollingState struct {
	shouldStop           bool
	seenIDs              []string
	processedCount       int
	gapAlerted           bool
	currentSubmissionID  *string
	previousSubmissionID *string
	submissionChanged    bool
}

// CommentPollingWorkflow continuously polls for new comments and processes them.
func CommentPollingWorkflow(ctx workflow.Context, seenIDs []string, currentSubmissionID, previousSubmissionID *string) (models.PollingStatus, error) {
	state := &commentPollingState{
		seenIDs:              seenIDs,
		currentSubmissionID:  currentSubmissionID,
		previousSubmissionID: previousSubmissionID,
	}
	if state.seenIDs == nil {
		state.seenIDs = []string{}
	}

	workflow.Go(ctx, func(ctx workflow.Context) {
		ch := workflow.GetSignalChannel(ctx, "stop")
		for {
			ch.Receive(ctx, nil)
			state.shouldStop = true
		}
	})
	workflow.Go(ctx, func(ctx workflow.Context) {
		ch := workflow.GetSignalChannel(ctx, "wake_up")
		for {
			ch.Receive(ctx, nil)
		}
	})
	workflow.Go(ctx, func(ctx workflow.Context) {
		ch := workflow.GetSignalChannel(ctx, "set_current_submission")
		for {
			var submissionID string
			ch.Receive(ctx, &submissionID)
			state.previousSubmissionID = state.currentSubmissionID
			id := submissionID
			state.currentSubmissionID = &id
			state.submissionChanged = true
		}
	})
	_ = workflow.SetQueryHandler(ctx, "get_status", func() (models.PollingStatus, error) {
		return pollingStatus(state, !state.shouldStop), nil
	})
	_ = workflow.SetQueryHandler(ctx, "get_submission_ids", func() (models.ActiveSubmissions, error) {
		return models.ActiveSubmissions{
			CurrentSubmissionID:  state.currentSubmissionID,
			PreviousSubmissionID: state.previousSubmissionID,
		}, nil
	})

	logger := workflow.GetLogger(ctx)
	logger.Info("Starting comment polling for subreddit")

	if state.currentSubmissionID == nil {
		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 60 * time.Second,
			RetryPolicy:         redditRetry,
			Summary:             "r/" + SubredditName,
		}
		var result models.ActiveSubmissions
		if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "fetch_active_submission_ids").Get(ctx, &result); err != nil {
			return models.PollingStatus{}, err
		}
		state.currentSubmissionID = result.CurrentSubmissionID
		state.previousSubmissionID = result.PreviousSubmissionID
		logger.Info("Bootstrapped submissions",
			"current", state.currentSubmissionID,
			"previous", state.previousSubmissionID)
	}

	for !state.shouldStop {
		info := workflow.GetInfo(ctx)
		if info.GetContinueAsNewSuggested() || info.GetTargetWorkerDeploymentVersionChanged() {
			logger.Info("Continuing as new")
			_ = workflow.UpsertTypedSearchAttributes(ctx, models.RedditSubreddit.ValueSet(SubredditName))
			return models.PollingStatus{}, workflow.NewContinueAsNewErrorWithOptions(ctx, workflow.ContinueAsNewErrorOptions{
				InitialVersioningBehavior: workflow.ContinueAsNewVersioningBehaviorAutoUpgrade,
			}, CommentPollingWorkflow, state.seenIDs, state.currentSubmissionID, state.previousSubmissionID)
		}

		state.submissionChanged = false
		var active []string
		if state.currentSubmissionID != nil {
			active = append(active, *state.currentSubmissionID)
		}
		if state.previousSubmissionID != nil {
			active = append(active, *state.previousSubmissionID)
		}
		current := ""
		if state.currentSubmissionID != nil {
			current = *state.currentSubmissionID
		}

		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 24 * time.Hour,
			HeartbeatTimeout:    60 * time.Second,
			RetryPolicy:         redditRetry,
		}
		actCtx, cancel := workflow.WithCancel(workflow.WithActivityOptions(ctx, ao))
		fut := workflow.ExecuteActivity(actCtx, "poll_new_comments", models.FetchCommentsInput{
			SeenIDs:             state.seenIDs,
			ActiveSubmissionIDs: active,
			CurrentSubmissionID: current,
		})

		_ = workflow.Await(ctx, func() bool {
			return fut.IsReady() || state.shouldStop || state.submissionChanged ||
				workflow.GetInfo(ctx).GetTargetWorkerDeploymentVersionChanged()
		})

		if !fut.IsReady() {
			cancel()
			err := fut.Get(ctx, nil)
			if err != nil && !temporal.IsCanceledError(err) && !isCanceledCause(err) {
				return models.PollingStatus{}, err
			}
			continue
		}
		cancel()

		var pollResult models.FetchCommentsResult
		if err := fut.Get(ctx, &pollResult); err != nil {
			return models.PollingStatus{}, err
		}

		if len(pollResult.ScannedIDs) > 0 {
			merged := append(pollResult.ScannedIDs, state.seenIDs...)
			if len(merged) > watermarkIDsMax {
				merged = merged[:watermarkIDsMax]
			}
			state.seenIDs = merged
		}

		if pollResult.PossibleGap {
			logger.Warn("Possible listing gap", "subreddit", SubredditName, "scanned", pollResult.ScannedCount)
			if !state.gapAlerted {
				nao := workflow.ActivityOptions{
					StartToCloseTimeout: 30 * time.Second,
					RetryPolicy:         pushoverRetry,
					Summary:             fmt.Sprintf("listing-gap:%d", pollResult.ScannedCount),
				}
				msg := fmt.Sprintf(
					"[r/%s] Possible comment listing gap: scanned %d comments without finding any previously-seen comment. Manual review of recent confirmations recommended.",
					SubredditName, pollResult.ScannedCount,
				)
				_ = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, nao), "send_pushover_notification", msg).Get(ctx, nil)
				state.gapAlerted = true
			}
		} else if pollResult.FoundSeen {
			state.gapAlerted = false
		}

		for _, commentData := range pollResult.Comments {
			if commentData.IsRoot && state.previousSubmissionID != nil &&
				commentData.SubmissionID == *state.previousSubmissionID {
				sao := workflow.ActivityOptions{
					StartToCloseTimeout: 30 * time.Second,
					RetryPolicy:         redditRetry,
					Summary:             commentData.ID,
				}
				_ = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, sao), "mark_comment_saved", commentData.ID).Get(ctx, nil)
				rao := workflow.ActivityOptions{
					StartToCloseTimeout: 30 * time.Second,
					RetryPolicy:         redditRetry,
					Summary:             commentData.ID + ":old_confirmation_thread",
				}
				_ = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, rao), "reply_to_comment", models.ReplyToCommentInput{
					CommentID:    commentData.ID,
					TemplateName: "old_confirmation_thread",
				}).Get(ctx, nil)
				state.processedCount++
				continue
			}

			childID := "process-" + commentData.ID
			childCtx := workflow.WithChildOptions(ctx, workflow.ChildWorkflowOptions{
				WorkflowID:            childID,
				TaskQueue:             TaskQueue,
				ParentClosePolicy:     enumspb.PARENT_CLOSE_POLICY_ABANDON,
				WorkflowIDReusePolicy: enumspb.WORKFLOW_ID_REUSE_POLICY_ALLOW_DUPLICATE_FAILED_ONLY,
				TypedSearchAttributes: temporal.NewSearchAttributes(
					models.RedditSubreddit.ValueSet(SubredditName),
					models.RedditCommentID.ValueSet(commentData.ID),
					models.RedditSubmissionID.ValueSet(commentData.SubmissionID),
					models.RedditConfirmationStatus.ValueSet("processing"),
				),
				StaticSummary: commentData.ID + ":u/" + commentData.AuthorName,
			})
			err := workflow.ExecuteChildWorkflow(childCtx, ProcessConfirmationWorkflow, commentData).GetChildWorkflowExecution().Get(ctx, nil)
			if err != nil {
				var alreadyStarted *temporal.ChildWorkflowExecutionAlreadyStartedError
				if temporal.IsWorkflowExecutionAlreadyStartedError(err) || errors.As(err, &alreadyStarted) {
					logger.Warn("Comment already has workflow, skipping start", "comment", commentData.ID)
				} else {
					logger.Warn("Failed to start child workflow", "comment", commentData.ID, "error", err)
				}
			} else {
				state.processedCount++
			}
		}
	}

	logger.Info("Comment polling stopped")
	return pollingStatus(state, false), nil
}

func pollingStatus(state *commentPollingState, running bool) models.PollingStatus {
	var lastSeen *string
	if len(state.seenIDs) > 0 {
		id := state.seenIDs[0]
		lastSeen = &id
	}
	return models.PollingStatus{
		LastSeenID:     lastSeen,
		ProcessedCount: state.processedCount,
		Running:        running,
		SeenIDsCount:   len(state.seenIDs),
	}
}

// ProcessConfirmationWorkflow processes a single comment for trade confirmation.
func ProcessConfirmationWorkflow(ctx workflow.Context, commentData models.CommentData) (models.ConfirmationResult, error) {
	commentID := commentData.ID
	author := commentData.AuthorName
	logger := workflow.GetLogger(ctx)
	logger.Info("Processing comment", "id", commentID, "author", author)

	save := func(id, summary string) error {
		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         redditRetry,
			Summary:             summary,
		}
		return workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "mark_comment_saved", id).Get(ctx, nil)
	}

	result, err := processConfirmation(ctx, commentData, save)
	if err != nil {
		_ = workflow.UpsertTypedSearchAttributes(ctx, models.RedditConfirmationStatus.ValueSet("manual_review"))
		logger.Error("Manual review required", "comment", commentID, "author", author, "error", err)
		nao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         pushoverRetry,
			Summary:             "manual-review:" + commentID,
		}
		msg := fmt.Sprintf("[r/%s] Manual review required for comment %s by u/%s: %v",
			SubredditName, commentID, author, err)
		_ = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, nao), "send_pushover_notification", msg).Get(ctx, nil)
		return models.ConfirmationResult{}, err
	}
	return result, nil
}

func processConfirmation(ctx workflow.Context, commentData models.CommentData, save func(string, string) error) (models.ConfirmationResult, error) {
	commentID := commentData.ID
	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 30 * time.Second,
		RetryPolicy:         redditRetry,
	}
	var validation models.ValidationResult
	if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "validate_confirmation", commentData).Get(ctx, &validation); err != nil {
		return models.ConfirmationResult{}, err
	}

	if !validation.Valid {
		if validation.Reason != "" {
			reply := models.ReplyToCommentInput{
				CommentID:    commentData.ID,
				TemplateName: validation.Reason,
				FormatArgs: map[string]string{
					"id":                commentData.ID,
					"body":              commentData.Body,
					"author_name":       commentData.AuthorName,
					"created_utc":       fmt.Sprintf("%g", commentData.CreatedUTC),
					"is_root":           fmt.Sprintf("%t", commentData.IsRoot),
					"submission_id":     commentData.SubmissionID,
					"parent_author":     validation.ParentAuthor,
					"parent_comment_id": validation.ParentCommentID,
				},
			}
			rao := workflow.ActivityOptions{
				StartToCloseTimeout: 30 * time.Second,
				RetryPolicy:         redditRetry,
				Summary:             reply.CommentID + ":" + reply.TemplateName,
			}
			if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, rao), "reply_to_comment", reply).Get(ctx, nil); err != nil {
				return models.ConfirmationResult{}, err
			}
			if err := save(commentID, commentID); err != nil {
				return models.ConfirmationResult{}, err
			}
			_ = workflow.UpsertTypedSearchAttributes(ctx, models.RedditConfirmationStatus.ValueSet("rejected"))
			return models.ConfirmationResult{
				Status:    "rejected",
				Reason:    validation.Reason,
				CommentID: commentID,
			}, nil
		}
		if err := save(commentID, commentID); err != nil {
			return models.ConfirmationResult{}, err
		}
		_ = workflow.UpsertTypedSearchAttributes(ctx, models.RedditConfirmationStatus.ValueSet("skipped"))
		return models.ConfirmationResult{
			Status:    "skipped",
			CommentID: commentID,
		}, nil
	}

	if validation.ParentAuthor == "" || validation.Confirmer == "" {
		return models.ConfirmationResult{}, fmt.Errorf("confirmed validation must include parent_author and confirmer")
	}
	key := rules.BuildConfirmationKey(validation.ParentCommentID, validation.Confirmer)
	parentReq := models.FlairIncrementRequest{
		Username:  validation.ParentAuthor,
		RequestID: key + ":parent",
		Delta:     1,
	}
	confirmerReq := models.FlairIncrementRequest{
		Username:  validation.Confirmer,
		RequestID: key + ":confirmer",
		Delta:     1,
	}

	fao := workflow.ActivityOptions{
		StartToCloseTimeout: 120 * time.Second,
		RetryPolicy:         redditRetry,
	}
	parentFut := workflow.ExecuteActivity(
		workflow.WithActivityOptions(ctx, fao),
		"request_flair_increment", parentReq,
	)
	confirmerFut := workflow.ExecuteActivity(
		workflow.WithActivityOptions(ctx, fao),
		"request_flair_increment", confirmerReq,
	)

	var parentResult, confirmerResult models.FlairIncrementResult
	if err := parentFut.Get(ctx, &parentResult); err != nil {
		return models.ConfirmationResult{}, err
	}
	if err := confirmerFut.Get(ctx, &confirmerResult); err != nil {
		return models.ConfirmationResult{}, err
	}

	if validation.ParentCommentID != "" {
		if err := save(validation.ParentCommentID, validation.ParentCommentID); err != nil {
			return models.ConfirmationResult{}, err
		}
	}

	replyCommentID := validation.ReplyToCommentID
	if replyCommentID == "" {
		replyCommentID = commentID
	}
	confirmationReply := models.ReplyToCommentInput{
		CommentID:    replyCommentID,
		TemplateName: "trade_confirmation",
		FormatArgs: map[string]string{
			"comment_id":        replyCommentID,
			"confirmer":         validation.Confirmer,
			"parent_author":     validation.ParentAuthor,
			"old_comment_flair": flairOrUnknown(confirmerResult.OldFlair),
			"new_comment_flair": flairOrUnknown(confirmerResult.NewFlair),
			"old_parent_flair":  flairOrUnknown(parentResult.OldFlair),
			"new_parent_flair":  flairOrUnknown(parentResult.NewFlair),
		},
	}
	rao := workflow.ActivityOptions{
		StartToCloseTimeout: 30 * time.Second,
		RetryPolicy:         redditRetry,
		Summary:             confirmationReply.CommentID + ":" + confirmationReply.TemplateName,
	}
	if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, rao), "reply_to_comment", confirmationReply).Get(ctx, nil); err != nil {
		return models.ConfirmationResult{}, err
	}
	if err := save(commentID, commentID); err != nil {
		return models.ConfirmationResult{}, err
	}

	elapsed := workflow.Now(ctx).Sub(time.Unix(int64(commentData.CreatedUTC), 0).UTC())
	workflow.GetLogger(ctx).Info(fmt.Sprintf(
		"Confirmed trade: %s (%s) <-> %s (%s) — %.1fs from comment to reply",
		validation.ParentAuthor,
		flairOrUnknown(parentResult.NewFlair),
		validation.Confirmer,
		flairOrUnknown(confirmerResult.NewFlair),
		elapsed.Seconds(),
	), "TaskQueue", TaskQueue)
	_ = workflow.UpsertTypedSearchAttributes(ctx, models.RedditConfirmationStatus.ValueSet("confirmed"))
	return models.ConfirmationResult{
		Status:            "confirmed",
		CommentID:         commentID,
		ParentAuthor:      validation.ParentAuthor,
		Confirmer:         validation.Confirmer,
		ParentNewFlair:    parentResult.NewFlair,
		ConfirmerNewFlair: confirmerResult.NewFlair,
	}, nil
}

func flairOrUnknown(p *string) string {
	if p == nil || *p == "" {
		return "unknown"
	}
	return *p
}

func isCanceledCause(err error) bool {
	for err != nil {
		if temporal.IsCanceledError(err) {
			return true
		}
		err = errors.Unwrap(err)
	}
	return false
}
