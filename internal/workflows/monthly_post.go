package workflows

import (
	"fmt"
	"time"

	"go.temporal.io/sdk/workflow"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/shared"
)

// MonthlyPostWorkflow creates the monthly confirmation thread and locks the old one after 5 days.
func MonthlyPostWorkflow(ctx workflow.Context) (map[string]any, error) {
	logger := workflow.GetLogger(ctx)
	logger.Info("Starting monthly post workflow", "subreddit", SubredditName)

	notify := func(msg, summary string) {
		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         shared.PushoverRetryPolicy(),
			Summary:             summary,
		}
		_ = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "send_pushover_notification", msg).Get(ctx, nil)
	}

	notify(fmt.Sprintf("Creating monthly post for r/%s", SubredditName), "monthly-start:r/"+SubredditName)

	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 60 * time.Second,
		RetryPolicy:         shared.RedditRetryPolicyConservative(),
		Summary:             "r/" + SubredditName,
	}
	var active models.ActiveSubmissions
	if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "fetch_active_submission_ids").Get(ctx, &active); err != nil {
		return nil, err
	}
	oldSubmissionID := active.CurrentSubmissionID

	summary := "prev:none"
	if oldSubmissionID != nil {
		summary = "prev:" + *oldSubmissionID
	}
	cao := workflow.ActivityOptions{
		StartToCloseTimeout: 60 * time.Second,
		RetryPolicy:         shared.RedditRetryPolicyConservative(),
		Summary:             summary,
	}
	var newSubmissionID string
	if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, cao), "create_monthly_post", models.CreateMonthlyPostInput{
		PreviousSubmissionID: oldSubmissionID,
	}).Get(ctx, &newSubmissionID); err != nil {
		return nil, err
	}

	// Signal polling workflow
	pollingID := "poll-" + SubredditName
	if err := workflow.SignalExternalWorkflow(ctx, pollingID, "", "set_current_submission", newSubmissionID).Get(ctx, nil); err != nil {
		logger.Warn("Could not signal polling workflow", "error", err)
	} else {
		logger.Info("Signalled polling workflow with new submission")
	}

	sao := workflow.ActivityOptions{
		StartToCloseTimeout: 60 * time.Second,
		RetryPolicy:         shared.RedditRetryPolicyConservative(),
		Summary:             newSubmissionID,
	}
	if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, sao), "sticky_submission", models.SubmissionInput{
		SubmissionID: newSubmissionID,
	}).Get(ctx, nil); err != nil {
		return nil, err
	}

	if oldSubmissionID != nil {
		uao := workflow.ActivityOptions{
			StartToCloseTimeout: 60 * time.Second,
			RetryPolicy:         shared.RedditRetryPolicyConservative(),
			Summary:             *oldSubmissionID,
		}
		if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, uao), "unsticky_submission", models.SubmissionInput{
			SubmissionID: *oldSubmissionID,
		}).Get(ctx, nil); err != nil {
			return nil, err
		}
	}

	notify(fmt.Sprintf("Monthly post for r/%s: %s", SubredditName, newSubmissionID), "monthly-post:"+newSubmissionID)
	logger.Info("Monthly post created", "id", newSubmissionID)

	if oldSubmissionID != nil {
		_ = workflow.Sleep(ctx, 5*24*time.Hour) // TimerOptions Summary not always available; plain sleep

		lao := workflow.ActivityOptions{
			StartToCloseTimeout: 60 * time.Second,
			RetryPolicy:         shared.RedditRetryPolicyConservative(),
			Summary:             *oldSubmissionID,
		}
		if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, lao), "lock_submission", models.SubmissionInput{
			SubmissionID: *oldSubmissionID,
		}).Get(ctx, nil); err != nil {
			return nil, err
		}
		notify(
			fmt.Sprintf("Locked previous submission %s for r/%s", *oldSubmissionID, SubredditName),
			"locked:"+*oldSubmissionID,
		)
		logger.Info("Locked previous submission", "id", *oldSubmissionID)
	}

	return map[string]any{
		"status":               "created",
		"submission_id":        newSubmissionID,
		"locked_submission_id": oldSubmissionID,
	}, nil
}
