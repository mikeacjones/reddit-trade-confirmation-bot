package workflows

import (
	"fmt"
	"time"

	enumspb "go.temporal.io/api/enums/v1"
	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/searchattr"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/services"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/shared"
)

// Set at worker startup from environment (one worker process per subreddit).
var (
	SubredditName string
	TaskQueue     string
)

type commentPollingState struct {
	shouldStop             bool
	seenIDs                []string
	processedCount         int
	gapAlerted             bool
	currentSubmissionID    *string
	previousSubmissionID   *string
	submissionChanged      bool
}

// CommentPollingWorkflow continuously polls for new comments and processes them.
func CommentPollingWorkflow(ctx workflow.Context, seenIDs []string, currentSubmissionID, previousSubmissionID *string) (map[string]any, error) {
	state := &commentPollingState{
		seenIDs:              seenIDs,
		currentSubmissionID:  currentSubmissionID,
		previousSubmissionID: previousSubmissionID,
	}
	if state.seenIDs == nil {
		state.seenIDs = []string{}
	}

	listenSignalEmpty(ctx, "stop", func() { state.shouldStop = true })
	listenSignalEmpty(ctx, "wake_up", func() {})
	listenSignal(ctx, "set_current_submission", func(submissionID string) {
		state.previousSubmissionID = state.currentSubmissionID
		id := submissionID
		state.currentSubmissionID = &id
		state.submissionChanged = true
	})
	_ = workflow.SetQueryHandler(ctx, "get_status", func() (map[string]any, error) {
		var lastSeen any
		if len(state.seenIDs) > 0 {
			lastSeen = state.seenIDs[0]
		}
		return map[string]any{
			"last_seen_id":    lastSeen,
			"processed_count": state.processedCount,
			"running":         !state.shouldStop,
			"seen_ids_count":  len(state.seenIDs),
		}, nil
	})
	_ = workflow.SetQueryHandler(ctx, "get_submission_ids", func() (map[string]any, error) {
		return map[string]any{
			"current_submission_id":  state.currentSubmissionID,
			"previous_submission_id": state.previousSubmissionID,
		}, nil
	})

	logger := workflow.GetLogger(ctx)
	logger.Info("Starting comment polling for subreddit")

	if state.currentSubmissionID == nil {
		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 60 * time.Second,
			RetryPolicy:         shared.RedditRetryPolicy(),
			Summary:             "r/" + SubredditName,
		}
		var result models.ActiveSubmissions
		if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "fetch_active_submission_ids").Get(ctx, &result); err != nil {
			return nil, err
		}
		state.currentSubmissionID = result.CurrentSubmissionID
		state.previousSubmissionID = result.PreviousSubmissionID
		logger.Info("Bootstrapped submissions",
			"current", ptrVal(state.currentSubmissionID),
			"previous", ptrVal(state.previousSubmissionID))
	}

	for !state.shouldStop {
		info := workflow.GetInfo(ctx)
		if info.GetContinueAsNewSuggested() || info.GetTargetWorkerDeploymentVersionChanged() {
			logger.Info("Continuing as new")
			return nil, continueAsNewAutoUpgrade(ctx, CommentPollingWorkflow,
				state.seenIDs, state.currentSubmissionID, state.previousSubmissionID)
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
			RetryPolicy:         shared.RedditRetryPolicy(),
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
			if err != nil && !temporal.IsCanceledError(err) && !isActivityCanceled(err) {
				return nil, err
			}
			continue
		}
		cancel()

		var pollResult models.FetchCommentsResult
		if err := fut.Get(ctx, &pollResult); err != nil {
			return nil, err
		}

		if len(pollResult.ScannedIDs) > 0 {
			merged := append(pollResult.ScannedIDs, state.seenIDs...)
			if len(merged) > shared.WatermarkIDsMax {
				merged = merged[:shared.WatermarkIDsMax]
			}
			state.seenIDs = merged
		}

		if pollResult.PossibleGap {
			logger.Warn("Possible listing gap", "subreddit", SubredditName, "scanned", pollResult.ScannedCount)
			if !state.gapAlerted {
				nao := workflow.ActivityOptions{
					StartToCloseTimeout: 30 * time.Second,
					RetryPolicy:         shared.PushoverRetryPolicy(),
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
					RetryPolicy:         shared.RedditRetryPolicy(),
					Summary:             commentData.ID,
				}
				_ = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, sao), "mark_comment_saved", commentData.ID).Get(ctx, nil)
				rao := workflow.ActivityOptions{
					StartToCloseTimeout: 30 * time.Second,
					RetryPolicy:         shared.RedditRetryPolicy(),
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
				TypedSearchAttributes: searchattr.ConfirmationSearchAttributes(
					SubredditName, commentData.ID, commentData.SubmissionID, "processing",
				),
				StaticSummary: commentData.ID + ":u/" + commentData.AuthorName,
			})
			err := workflow.ExecuteChildWorkflow(childCtx, ProcessConfirmationWorkflow, commentData).GetChildWorkflowExecution().Get(ctx, nil)
			if err != nil {
				if isAlreadyStarted(err) {
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
	var lastSeen any
	if len(state.seenIDs) > 0 {
		lastSeen = state.seenIDs[0]
	}
	return map[string]any{
		"last_seen_id":    lastSeen,
		"processed_count": state.processedCount,
		"running":         false,
		"seen_ids_count":  len(state.seenIDs),
	}, nil
}

// ProcessConfirmationWorkflow processes a single comment for trade confirmation.
func ProcessConfirmationWorkflow(ctx workflow.Context, commentData models.CommentData) (map[string]any, error) {
	commentID := commentData.ID
	author := commentData.AuthorName
	logger := workflow.GetLogger(ctx)
	logger.Info("Processing comment", "id", commentID, "author", author)

	save := func(id, summary string) error {
		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         shared.RedditRetryPolicy(),
			Summary:             summary,
		}
		return workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "mark_comment_saved", id).Get(ctx, nil)
	}

	result, err := processConfirmation(ctx, commentData, save)
	if err != nil {
		_ = workflow.UpsertTypedSearchAttributes(ctx, searchattr.RedditConfirmationStatus.ValueSet("manual_review"))
		logger.Error("Manual review required", "comment", commentID, "author", author, "error", err)
		nao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         shared.PushoverRetryPolicy(),
			Summary:             "manual-review:" + commentID,
		}
		msg := fmt.Sprintf("[r/%s] Manual review required for comment %s by u/%s: %v",
			SubredditName, commentID, author, err)
		_ = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, nao), "send_pushover_notification", msg).Get(ctx, nil)
		return nil, err
	}
	return result, nil
}

func processConfirmation(ctx workflow.Context, commentData models.CommentData, save func(string, string) error) (map[string]any, error) {
	commentID := commentData.ID
	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 30 * time.Second,
		RetryPolicy:         shared.RedditRetryPolicy(),
	}
	var validation models.ValidationResult
	if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "validate_confirmation", commentData).Get(ctx, &validation); err != nil {
		return nil, err
	}

	if !validation.Valid {
		reply := services.BuildInvalidReply(commentData, validation)
		if reply != nil {
			rao := workflow.ActivityOptions{
				StartToCloseTimeout: 30 * time.Second,
				RetryPolicy:         shared.RedditRetryPolicy(),
				Summary:             reply.CommentID + ":" + reply.TemplateName,
			}
			if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, rao), "reply_to_comment", *reply).Get(ctx, nil); err != nil {
				return nil, err
			}
			if err := save(commentID, commentID); err != nil {
				return nil, err
			}
			_ = workflow.UpsertTypedSearchAttributes(ctx, searchattr.RedditConfirmationStatus.ValueSet("rejected"))
			return map[string]any{
				"status":     "rejected",
				"reason":     validation.Reason,
				"comment_id": commentID,
			}, nil
		}
		if err := save(commentID, commentID); err != nil {
			return nil, err
		}
		_ = workflow.UpsertTypedSearchAttributes(ctx, searchattr.RedditConfirmationStatus.ValueSet("skipped"))
		return map[string]any{
			"status":     "skipped",
			"comment_id": commentID,
		}, nil
	}

	parentReq, confirmerReq, err := services.BuildFlairIncrementRequests(validation)
	if err != nil {
		return nil, err
	}

	fao := workflow.ActivityOptions{
		StartToCloseTimeout: 120 * time.Second,
		RetryPolicy:         shared.RedditRetryPolicy(),
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
		return nil, err
	}
	if err := confirmerFut.Get(ctx, &confirmerResult); err != nil {
		return nil, err
	}

	if validation.ParentCommentID != "" {
		if err := save(validation.ParentCommentID, validation.ParentCommentID); err != nil {
			return nil, err
		}
	}

	confirmationReply := services.BuildConfirmationReply(commentID, validation, parentResult, confirmerResult)
	rao := workflow.ActivityOptions{
		StartToCloseTimeout: 30 * time.Second,
		RetryPolicy:         shared.RedditRetryPolicy(),
		Summary:             confirmationReply.CommentID + ":" + confirmationReply.TemplateName,
	}
	if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, rao), "reply_to_comment", confirmationReply).Get(ctx, nil); err != nil {
		return nil, err
	}
	if err := save(commentID, commentID); err != nil {
		return nil, err
	}

	elapsed := workflow.Now(ctx).Sub(time.Unix(int64(commentData.CreatedUTC), 0).UTC())
	workflow.GetLogger(ctx).Info("Confirmed trade",
		"parent", validation.ParentAuthor,
		"confirmer", validation.Confirmer,
		"elapsed", elapsed.Seconds())
	_ = workflow.UpsertTypedSearchAttributes(ctx, searchattr.RedditConfirmationStatus.ValueSet("confirmed"))
	return services.BuildConfirmedResult(commentID, validation, parentResult, confirmerResult), nil
}

func continueAsNewAutoUpgrade(ctx workflow.Context, wfn any, args ...any) error {
	_ = workflow.UpsertTypedSearchAttributes(ctx, searchattr.RedditSubreddit.ValueSet(SubredditName))
	return workflow.NewContinueAsNewErrorWithOptions(ctx, workflow.ContinueAsNewErrorOptions{
		InitialVersioningBehavior: workflow.ContinueAsNewVersioningBehaviorAutoUpgrade,
	}, wfn, args...)
}

func ptrVal(p *string) string {
	if p == nil {
		return "<nil>"
	}
	return *p
}

func isActivityCanceled(err error) bool {
	var appErr *temporal.ApplicationError
	if temporal.IsCanceledError(err) {
		return true
	}
	if temporal.IsTimeoutError(err) {
		return false
	}
	_ = appErr
	// Unwrap activity error cause
	cause := err
	for cause != nil {
		if temporal.IsCanceledError(cause) {
			return true
		}
		u, ok := cause.(interface{ Unwrap() error })
		if !ok {
			break
		}
		cause = u.Unwrap()
	}
	return false
}

func isAlreadyStarted(err error) bool {
	type already interface{ AlreadyStarted() bool }
	if _, ok := err.(already); ok {
		return true
	}
	s := err.Error()
	return contains(s, "already started") || contains(s, "AlreadyStarted")
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 ||
		(func() bool {
			for i := 0; i+len(sub) <= len(s); i++ {
				if s[i:i+len(sub)] == sub {
					return true
				}
			}
			return false
		})())
}
