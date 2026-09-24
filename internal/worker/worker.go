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

	type named struct {
		fn   any
		name string
	}
	for _, a := range []named{
		{acts.PollNewComments, "poll_new_comments"},
		{acts.ValidateConfirmation, "validate_confirmation"},
		{acts.MarkCommentSaved, "mark_comment_saved"},
		{acts.ReplyToComment, "reply_to_comment"},
		{acts.GetUserFlair, "get_user_flair"},
		{acts.SetUserFlair, "set_user_flair"},
		{acts.RequestFlairIncrement, "request_flair_increment"},
		{acts.FetchActiveSubmissionIDs, "fetch_active_submission_ids"},
		{acts.StickySubmission, "sticky_submission"},
		{acts.UnstickySubmission, "unsticky_submission"},
		{acts.LockSubmission, "lock_submission"},
		{acts.CreateMonthlyPost, "create_monthly_post"},
		{acts.SendPushoverNotification, "send_pushover_notification"},
		{acts.DescribeWorkerDeployment, "describe_worker_deployment"},
		{acts.CountFailedWorkflowsForDeployment, "count_failed_workflows_for_deployment"},
		{acts.CountCompletedWorkflowsForDeployment, "count_completed_workflows_for_deployment"},
		{acts.CollectSDKMetrics, "collect_sdk_metrics"},
		{acts.ListDeploymentContainers, "list_deployment_containers"},
		{acts.RollbackWorkerDeployment, "rollback_worker_deployment"},
		{acts.RemoveDeploymentContainer, "remove_deployment_container"},
	} {
		w.RegisterActivityWithOptions(a.fn, activity.RegisterOptions{Name: a.name})
	}
}
