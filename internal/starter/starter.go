package starter

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"strconv"
	"strings"
	"time"

	enumspb "go.temporal.io/api/enums/v1"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/worker"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/config"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/deployment"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/searchattr"
	wf "github.com/mikeacjones/reddit-trade-confirmation-bot/internal/workflows"
)

// Run dispatches a starter CLI command.
func Run(args []string) error {
	if len(args) < 1 {
		printUsage()
		return nil
	}
	cfg := config.LoadWithoutReddit()
	wf.SubredditName = cfg.SubredditName
	wf.TaskQueue = cfg.TaskQueue

	c, err := client.Dial(client.Options{
		HostPort:  cfg.TemporalHost,
		Namespace: cfg.TemporalNamespace,
	})
	if err != nil {
		return err
	}
	defer c.Close()

	ctx := context.Background()
	cmd := args[0]
	rest := args[1:]

	switch cmd {
	case "setup":
		return setupSchedules(ctx, c, cfg)
	case "start-polling":
		return startPolling(ctx, c, cfg)
	case "create-monthly":
		return triggerMonthlyPost(ctx, c, cfg)
	case "delete-lock-schedule":
		return deleteLockSchedule(ctx, c, cfg)
	case "status":
		return showStatus(ctx, c, cfg)
	case "deployment-signal-with-start":
		if len(rest) < 1 {
			return fmt.Errorf("missing build ID")
		}
		deploymentName := cfg.DeploymentName
		if len(rest) > 1 {
			deploymentName = rest[1]
		}
		return signalDeploymentCleanup(ctx, c, cfg, rest[0], deploymentName)
	case "deployment-current-build":
		deploymentName := cfg.DeploymentName
		if len(rest) > 0 {
			deploymentName = rest[0]
		}
		return printCurrentDeploymentBuildID(ctx, c, deploymentName)
	default:
		printUsage()
		return fmt.Errorf("unknown command: %s", cmd)
	}
}

func subredditAttrs(name string) temporal.SearchAttributes {
	return temporal.NewSearchAttributes(searchattr.RedditSubreddit.ValueSet(name))
}

func setupSchedules(ctx context.Context, c client.Client, cfg config.Config) error {
	if err := searchattr.EnsureSearchAttributes(ctx, c, cfg.TemporalNamespace); err != nil {
		return err
	}
	slog.Info("Setting up schedules...")

	scheduleID := "monthly-post-schedule-" + cfg.SubredditName
	action := &client.ScheduleWorkflowAction{
		ID:                    "monthly-post-" + cfg.SubredditName,
		Workflow:              "MonthlyPostWorkflow",
		TaskQueue:             cfg.TaskQueue,
		TypedSearchAttributes: subredditAttrs(cfg.SubredditName),
		StaticSummary:         "r/" + cfg.SubredditName,
	}
	spec := client.ScheduleSpec{
		Calendars: []client.ScheduleCalendarSpec{{
			DayOfMonth: []client.ScheduleRange{{Start: 1}},
			Hour:       []client.ScheduleRange{{Start: 0}},
			Minute:     []client.ScheduleRange{{Start: 0}},
		}},
	}

	_, err := c.ScheduleClient().Create(ctx, client.ScheduleOptions{
		ID:     scheduleID,
		Spec:   spec,
		Action: action,
	})
	if err != nil {
		if !strings.Contains(strings.ToLower(err.Error()), "already") {
			return err
		}
		handle := c.ScheduleClient().GetHandle(ctx, scheduleID)
		err = handle.Update(ctx, client.ScheduleUpdateOptions{
			DoUpdate: func(input client.ScheduleUpdateInput) (*client.ScheduleUpdate, error) {
				s := input.Description.Schedule
				s.Action = action
				s.Spec = &spec
				return &client.ScheduleUpdate{Schedule: &s}, nil
			},
		})
		if err != nil {
			return err
		}
		slog.Info("Updated schedule", "id", scheduleID)
		return nil
	}
	slog.Info("Created schedule", "id", scheduleID)
	return nil
}

func startPolling(ctx context.Context, c client.Client, cfg config.Config) error {
	if err := searchattr.EnsureSearchAttributes(ctx, c, cfg.TemporalNamespace); err != nil {
		return err
	}
	workflowID := "poll-" + cfg.SubredditName
	slog.Info("Starting comment polling", "subreddit", cfg.SubredditName)
	_, err := c.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
		ID:                    workflowID,
		TaskQueue:             cfg.TaskQueue,
		TypedSearchAttributes: subredditAttrs(cfg.SubredditName),
		StaticSummary:         "r/" + cfg.SubredditName,
	}, "CommentPollingWorkflow", []string(nil), (*string)(nil), (*string)(nil))
	if err != nil {
		if temporal.IsWorkflowExecutionAlreadyStartedError(err) {
			slog.Info("Polling workflow already running", "id", workflowID)
			return nil
		}
		return err
	}
	slog.Info("Started polling workflow", "id", workflowID)
	return nil
}

func triggerMonthlyPost(ctx context.Context, c client.Client, cfg config.Config) error {
	if err := searchattr.EnsureSearchAttributes(ctx, c, cfg.TemporalNamespace); err != nil {
		return err
	}
	slog.Info("Triggering monthly post workflow...")
	run, err := c.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
		ID:                    "monthly-post-manual-" + cfg.SubredditName,
		TaskQueue:             cfg.TaskQueue,
		TypedSearchAttributes: subredditAttrs(cfg.SubredditName),
		StaticSummary:         "r/" + cfg.SubredditName,
	}, "MonthlyPostWorkflow")
	if err != nil {
		return err
	}
	var result map[string]any
	if err := run.Get(ctx, &result); err != nil {
		return err
	}
	slog.Info("Monthly post result", "result", result)
	return nil
}

func deleteLockSchedule(ctx context.Context, c client.Client, cfg config.Config) error {
	scheduleID := "lock-submissions-schedule-" + cfg.SubredditName
	handle := c.ScheduleClient().GetHandle(ctx, scheduleID)
	if err := handle.Delete(ctx); err != nil {
		slog.Info("Schedule not found or already deleted", "id", scheduleID, "error", err)
		return nil
	}
	slog.Info("Deleted schedule", "id", scheduleID)
	return nil
}

func showStatus(ctx context.Context, c client.Client, cfg config.Config) error {
	workflowID := "poll-" + cfg.SubredditName
	desc, err := c.DescribeWorkflowExecution(ctx, workflowID, "")
	if err != nil {
		slog.Info("Polling workflow not found", "error", err)
	} else {
		slog.Info("Polling workflow", "status", desc.WorkflowExecutionInfo.Status.String())
		var status map[string]any
		if enc, err := c.QueryWorkflow(ctx, workflowID, "", "get_status"); err == nil {
			if err := enc.Get(&status); err == nil {
				slog.Info("Status", "processed", status["processed_count"], "last_seen", status["last_seen_id"])
			}
		}
		var subs map[string]any
		if enc, err := c.QueryWorkflow(ctx, workflowID, "", "get_submission_ids"); err == nil {
			if err := enc.Get(&subs); err == nil {
				slog.Info("Submissions", "current", subs["current_submission_id"], "previous", subs["previous_submission_id"])
			}
		}
	}
	slog.Info("Schedules:")
	iter, err := c.ScheduleClient().List(ctx, client.ScheduleListOptions{})
	if err != nil {
		return err
	}
	for iter.HasNext() {
		entry, err := iter.Next()
		if err != nil {
			return err
		}
		slog.Info("  schedule", "id", entry.ID)
	}
	return nil
}

func signalDeploymentCleanup(ctx context.Context, c client.Client, cfg config.Config, buildID, deploymentName string) error {
	if err := searchattr.EnsureSearchAttributes(ctx, c, cfg.TemporalNamespace); err != nil {
		return err
	}
	workflowID := "deployment-cleanup-" + cfg.SubredditSlug
	healthCheck := &deployment.HealthCheck{
		BuildID:                     buildID,
		MaxMonitorSeconds:           envInt("DEPLOYMENT_HEALTH_MAX_SECONDS", 0),
		CheckIntervalSeconds:        envInt("DEPLOYMENT_HEALTH_CHECK_INTERVAL_SECONDS", 60),
		RequireMetrics:              strings.EqualFold(os.Getenv("DEPLOYMENT_HEALTH_REQUIRE_METRICS"), "true"),
		RequiredCompletedWorkflows:  envInt("DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_WORKFLOWS", 5),
		RequiredCompletedActivities: envInt("DEPLOYMENT_HEALTH_REQUIRED_COMPLETED_ACTIVITIES", 5),
		MaxFailedWorkflows:          envInt("DEPLOYMENT_HEALTH_MAX_FAILED_WORKFLOWS", 0),
		MaxSDKWorkflowFailures:      envInt("DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_FAILURES", 0),
		MaxSDKActivityFailures:      envInt("DEPLOYMENT_HEALTH_MAX_SDK_ACTIVITY_FAILURES", 0),
		MaxSDKWorkflowTaskFailures:  envInt("DEPLOYMENT_HEALTH_MAX_SDK_WORKFLOW_TASK_FAILURES", 0),
	}
	if v := os.Getenv("DEPLOYMENT_PREVIOUS_BUILD_ID"); v != "" {
		healthCheck.PreviousBuildID = &v
	}
	if v := os.Getenv("DEPLOYMENT_CONTAINER_NAME"); v != "" {
		healthCheck.ContainerName = &v
	}
	if v := os.Getenv("DEPLOYMENT_HEALTH_METRICS_URL"); v != "" {
		healthCheck.MetricsURL = &v
	}

	signal := deployment.DeployedSignal{BuildID: buildID, HealthCheck: healthCheck}
	waitSeconds := envInt("DEPLOYMENT_WORKER_START_WAIT_SECONDS", 300)
	retrySeconds := envInt("DEPLOYMENT_WORKER_START_RETRY_SECONDS", 5)
	deadline := time.Now().Add(time.Duration(waitSeconds) * time.Second)
	attempt := 1

	for {
		run, err := c.SignalWithStartWorkflow(ctx, workflowID, "deployed", signal,
			client.StartWorkflowOptions{
				ID:                       workflowID,
				TaskQueue:                cfg.TaskQueue,
				WorkflowIDConflictPolicy: enumspb.WORKFLOW_ID_CONFLICT_POLICY_TERMINATE_EXISTING,
				TypedSearchAttributes:    subredditAttrs(cfg.SubredditName),
				StaticSummary:            "r/" + cfg.SubredditName + " deployment cleanup",
				VersioningOverride: &client.PinnedVersioningOverride{
					Version: worker.WorkerDeploymentVersion{
						DeploymentName: deploymentName,
						BuildID:        buildID,
					},
				},
			},
			"DeploymentCleanupWorkflow", deploymentName, cfg.SubredditName, (*deployment.CleanupState)(nil),
		)
		if err == nil {
			slog.Info("Signal-with-start sent",
				"workflow_id", workflowID,
				"deployment", deploymentName,
				"build_id", buildID,
				"run_id", run.GetRunID())
			return nil
		}
		if !isWorkerVersionNotReady(err) || time.Now().After(deadline) {
			return err
		}
		slog.Info("Waiting for workflow poller",
			"deployment", deploymentName, "build_id", buildID,
			"attempt", attempt, "error", err)
		attempt++
		time.Sleep(time.Duration(max(1, retrySeconds)) * time.Second)
	}
}

func printCurrentDeploymentBuildID(ctx context.Context, c client.Client, deploymentName string) error {
	handle := c.WorkerDeploymentClient().GetHandle(deploymentName)
	resp, err := handle.Describe(ctx, client.WorkerDeploymentDescribeOptions{})
	if err != nil {
		return err
	}
	buildID := ""
	if resp.Info.RoutingConfig.CurrentVersion != nil {
		buildID = resp.Info.RoutingConfig.CurrentVersion.BuildID
	}
	fmt.Println(buildID)
	return nil
}

func isWorkerVersionNotReady(err error) bool {
	s := err.Error()
	return strings.Contains(s, "Pinned version") && strings.Contains(s, "not present in task queue")
}

func envInt(key string, def int) int {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return def
	}
	return n
}

func printUsage() {
	fmt.Print(`Usage: reddit-bot <command>

Commands:
    worker              Start the Temporal worker
    setup               Register search attributes and set up schedules
    start-polling       Start polling for comments on current submission
    create-monthly      Manually trigger monthly post creation
    delete-lock-schedule  Delete stale lock-submissions schedule
    status              Show status of running workflows
    deployment-signal-with-start <build-id> [deployment-name]
                        Start/signal Docker deployment cleanup
    deployment-current-build [deployment-name]
                        Print current Worker Deployment build ID
`)
}
