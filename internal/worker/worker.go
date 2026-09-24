package worker

import (
	"log/slog"
	"os"

	"go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/worker"
	"go.temporal.io/sdk/workflow"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/activities"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/config"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/metrics"
	wf "github.com/mikeacjones/reddit-trade-confirmation-bot/internal/workflows"
)

// Run starts the Temporal worker and blocks until interrupt.
func Run() error {
	cfg := config.MustLoad()
	wf.SubredditName = cfg.SubredditName
	wf.TaskQueue = cfg.TaskQueue

	opts := client.Options{
		HostPort:  cfg.TemporalHost,
		Namespace: cfg.TemporalNamespace,
	}

	if bind := os.Getenv("TEMPORAL_SDK_METRICS_BIND_ADDRESS"); bind != "" {
		h, err := metrics.New(bind, map[string]string{
			"app":             "reddit-trade-confirmation-bot",
			"subreddit":       cfg.SubredditName,
			"deployment_name": cfg.DeploymentName,
			"build_id":        cfg.BuildID,
		})
		if err != nil {
			return err
		}
		opts.MetricsHandler = h
		slog.Info("Temporal SDK metrics enabled", "url", "http://"+bind+"/metrics")
		defer h.Close()
	}

	c, err := client.Dial(opts)
	if err != nil {
		return err
	}
	defer c.Close()

	slog.Info("Connected to Temporal", "host", cfg.TemporalHost, "namespace", cfg.TemporalNamespace)
	slog.Info("Starting worker", "task_queue", cfg.TaskQueue, "subreddit", cfg.SubredditName,
		"deployment", cfg.DeploymentName, "build_id", cfg.BuildID)

	acts := activities.New(cfg, c)
	w := worker.New(c, cfg.TaskQueue, worker.Options{
		DeploymentOptions: worker.DeploymentOptions{
			UseVersioning: true,
			Version: worker.WorkerDeploymentVersion{
				DeploymentName: cfg.DeploymentName,
				BuildID:        cfg.BuildID,
			},
			DefaultVersioningBehavior: workflow.VersioningBehaviorPinned,
		},
	})

	register(w, acts)

	slog.Info("Worker started")
	return w.Run(worker.InterruptCh())
}

func register(w worker.Worker, acts *activities.Activities) {
	w.RegisterWorkflowWithOptions(wf.CommentPollingWorkflow, workflow.RegisterOptions{
		Name:               "CommentPollingWorkflow",
		VersioningBehavior: workflow.VersioningBehaviorPinned,
	})
	w.RegisterWorkflowWithOptions(wf.ProcessConfirmationWorkflow, workflow.RegisterOptions{
		Name:               "ProcessConfirmationWorkflow",
		VersioningBehavior: workflow.VersioningBehaviorPinned,
	})
	w.RegisterWorkflowWithOptions(wf.FlairCoordinatorWorkflow, workflow.RegisterOptions{
		Name:               "FlairCoordinatorWorkflow",
		VersioningBehavior: workflow.VersioningBehaviorPinned,
	})
	w.RegisterWorkflowWithOptions(wf.MonthlyPostWorkflow, workflow.RegisterOptions{
		Name:               "MonthlyPostWorkflow",
		VersioningBehavior: workflow.VersioningBehaviorPinned,
	})
	w.RegisterWorkflowWithOptions(wf.DeploymentCleanupWorkflow, workflow.RegisterOptions{
		Name:               "DeploymentCleanupWorkflow",
		VersioningBehavior: workflow.VersioningBehaviorUnspecified,
	})

	w.RegisterActivityWithOptions(acts.PollNewComments, activity.RegisterOptions{Name: "poll_new_comments"})
	w.RegisterActivityWithOptions(acts.ValidateConfirmation, activity.RegisterOptions{Name: "validate_confirmation"})
	w.RegisterActivityWithOptions(acts.MarkCommentSaved, activity.RegisterOptions{Name: "mark_comment_saved"})
	w.RegisterActivityWithOptions(acts.ReplyToComment, activity.RegisterOptions{Name: "reply_to_comment"})
	w.RegisterActivityWithOptions(acts.GetUserFlair, activity.RegisterOptions{Name: "get_user_flair"})
	w.RegisterActivityWithOptions(acts.SetUserFlair, activity.RegisterOptions{Name: "set_user_flair"})
	w.RegisterActivityWithOptions(acts.RequestFlairIncrement, activity.RegisterOptions{Name: "request_flair_increment"})
	w.RegisterActivityWithOptions(acts.FetchActiveSubmissionIDs, activity.RegisterOptions{Name: "fetch_active_submission_ids"})
	w.RegisterActivityWithOptions(acts.StickySubmission, activity.RegisterOptions{Name: "sticky_submission"})
	w.RegisterActivityWithOptions(acts.UnstickySubmission, activity.RegisterOptions{Name: "unsticky_submission"})
	w.RegisterActivityWithOptions(acts.LockSubmission, activity.RegisterOptions{Name: "lock_submission"})
	w.RegisterActivityWithOptions(acts.CreateMonthlyPost, activity.RegisterOptions{Name: "create_monthly_post"})
	w.RegisterActivityWithOptions(acts.SendPushoverNotification, activity.RegisterOptions{Name: "send_pushover_notification"})
	w.RegisterActivityWithOptions(acts.DescribeWorkerDeployment, activity.RegisterOptions{Name: "describe_worker_deployment"})
	w.RegisterActivityWithOptions(acts.CountFailedWorkflowsForDeployment, activity.RegisterOptions{Name: "count_failed_workflows_for_deployment"})
	w.RegisterActivityWithOptions(acts.CountCompletedWorkflowsForDeployment, activity.RegisterOptions{Name: "count_completed_workflows_for_deployment"})
	w.RegisterActivityWithOptions(acts.CollectSDKMetrics, activity.RegisterOptions{Name: "collect_sdk_metrics"})
	w.RegisterActivityWithOptions(acts.ListDeploymentContainers, activity.RegisterOptions{Name: "list_deployment_containers"})
	w.RegisterActivityWithOptions(acts.RollbackWorkerDeployment, activity.RegisterOptions{Name: "rollback_worker_deployment"})
	w.RegisterActivityWithOptions(acts.RemoveDeploymentContainer, activity.RegisterOptions{Name: "remove_deployment_container"})
}
