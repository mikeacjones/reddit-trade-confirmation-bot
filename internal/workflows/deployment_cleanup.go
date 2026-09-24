package workflows

import (
	"fmt"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/models"
	"time"

	"go.temporal.io/sdk/workflow"
)

var checkInterval = 30 * time.Second

// DeploymentCleanupWorkflow monitors Worker Deployment drainage and removes old Docker containers.
func DeploymentCleanupWorkflow(ctx workflow.Context, deploymentName, subredditName string, state *models.CleanupState) (models.CleanupStatus, error) {
	if state == nil {
		state = &models.CleanupState{}
	}

	workflow.Go(ctx, func(ctx workflow.Context) {
		ch := workflow.GetSignalChannel(ctx, "deployed")
		for {
			var signal models.DeployedSignal
			ch.Receive(ctx, &signal)
			buildID := signal.BuildID
			found := false
			for _, id := range state.SeenBuildIDs {
				if id == buildID {
					found = true
					break
				}
			}
			if !found {
				state.SeenBuildIDs = append(state.SeenBuildIDs, buildID)
			}
			state.CurrentBuildID = &buildID
			if signal.HealthCheck == nil {
				hc := models.HealthCheck{BuildID: buildID}
				state.HealthCheck = &hc
			} else {
				state.HealthCheck = signal.HealthCheck
			}
			state.MonitorStartedAt = nil
			state.MonitorDeadline = nil
			state.BaselineMetrics = nil
			state.HealthPassed = false
			state.LastCompletedWorkflows = 0
			state.LastCompletedActivities = 0
			state.RolledBack = false
			state.RollbackReason = nil
		}
	})
	_ = workflow.SetQueryHandler(ctx, "get_status", func() (models.CleanupStatus, error) {
		return cleanupStatus(deploymentName, subredditName, state), nil
	})

	_ = workflow.Await(ctx, func() bool { return state.CurrentBuildID != nil })

	for {
		currentBuildID := state.CurrentBuildID
		if currentBuildID == nil {
			_ = workflow.Sleep(ctx, checkInterval)
			continue
		}

		if err := continueCleanupAsNewIfSuggested(ctx, deploymentName, subredditName, state); err != nil {
			return models.CleanupStatus{}, err
		}

		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         deploymentRetry,
			Summary:             deploymentName,
		}
		var depState models.WorkerDeploymentState
		err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "describe_worker_deployment", deploymentName).Get(ctx, &depState)
		if err != nil {
			workflow.GetLogger(ctx).Warn("Deployment inspection failed", "error", err)
			if err2 := continueCleanupAsNewIfSuggested(ctx, deploymentName, subredditName, state); err2 != nil {
				return models.CleanupStatus{}, err2
			}
			_ = workflow.Sleep(ctx, checkInterval)
			continue
		}

		cao := workflow.ActivityOptions{
			StartToCloseTimeout: 15 * time.Second,
			RetryPolicy:         deploymentRetry,
			Summary:             deploymentName,
		}
		var containers []models.DockerContainerState
		err = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, cao), "list_deployment_containers", deploymentName).Get(ctx, &containers)
		if err != nil {
			workflow.GetLogger(ctx).Warn("Deployment inspection failed", "error", err)
			if err2 := continueCleanupAsNewIfSuggested(ctx, deploymentName, subredditName, state); err2 != nil {
				return models.CleanupStatus{}, err2
			}
			_ = workflow.Sleep(ctx, checkInterval)
			continue
		}

		var activeVersions []models.WorkerVersionState
		containersByBuild := map[string]models.DockerContainerState{}
		state.LastActiveBuildIDs = nil
		state.LastContainerBuildIDs = nil
		for _, v := range depState.Versions {
			if v.IsActive() {
				activeVersions = append(activeVersions, v)
				state.LastActiveBuildIDs = append(state.LastActiveBuildIDs, v.BuildID)
			}
		}
		for _, c := range containers {
			containersByBuild[c.BuildID] = c
			state.LastContainerBuildIDs = append(state.LastContainerBuildIDs, c.BuildID)
		}

		if err := continueCleanupAsNewIfSuggested(ctx, deploymentName, subredditName, state); err != nil {
			return models.CleanupStatus{}, err
		}

		rolledBack, err := rollbackIfUnhealthy(ctx, deploymentName, subredditName, *currentBuildID, containers, state)
		if err != nil {
			return models.CleanupStatus{}, err
		}
		if rolledBack {
			return cleanupStatus(deploymentName, subredditName, state), nil
		}

		if state.HealthCheck != nil && !state.HealthPassed {
			if err := continueCleanupAsNewIfSuggested(ctx, deploymentName, subredditName, state); err != nil {
				return models.CleanupStatus{}, err
			}
			_ = workflow.Sleep(ctx, healthCheckInterval(state.HealthCheck))
			continue
		}

		cleanupFailed := false
		for _, version := range depState.Versions {
			if version.BuildID == *currentBuildID || !version.IsCleanupReady() {
				continue
			}
			container, ok := containersByBuild[version.BuildID]
			if !ok {
				continue
			}
			rao := workflow.ActivityOptions{
				StartToCloseTimeout: 60 * time.Second,
				RetryPolicy:         deploymentRetry,
				Summary:             container.Name,
			}
			var result models.DockerCleanupResult
			err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, rao), "remove_deployment_container", container).Get(ctx, &result)
			if err != nil {
				workflow.GetLogger(ctx).Warn("Failed to remove drained container", "build", version.BuildID, "error", err)
				cleanupFailed = true
				continue
			}
			state.CleanupCount++
			if result.ImageRemoveError != nil {
				workflow.GetLogger(ctx).Warn("Removed container but could not remove image",
					"container", result.ContainerName, "error", *result.ImageRemoveError)
			} else {
				workflow.GetLogger(ctx).Info("Removed drained deployment container", "container", result.ContainerName)
			}
		}

		if len(activeVersions) <= 1 && !cleanupFailed {
			workflow.GetLogger(ctx).Info("Deployment cleanup complete",
				"deployment", deploymentName, "active", state.LastActiveBuildIDs)
			return cleanupStatus(deploymentName, subredditName, state), nil
		}

		if err := continueCleanupAsNewIfSuggested(ctx, deploymentName, subredditName, state); err != nil {
			return models.CleanupStatus{}, err
		}
		_ = workflow.Sleep(ctx, checkInterval)
	}
}

func cleanupStatus(deploymentName, subredditName string, state *models.CleanupState) models.CleanupStatus {
	return models.CleanupStatus{
		CurrentBuildID:      state.CurrentBuildID,
		SeenBuildIDs:        state.SeenBuildIDs,
		DeploymentName:      deploymentName,
		SubredditName:       subredditName,
		ActiveBuildIDs:      state.LastActiveBuildIDs,
		ContainerBuildIDs:   state.LastContainerBuildIDs,
		CleanupCount:        state.CleanupCount,
		MonitorStartedAt:    state.MonitorStartedAt,
		MonitorDeadline:     state.MonitorDeadline,
		HealthPassed:        state.HealthPassed,
		CompletedWorkflows:  state.LastCompletedWorkflows,
		CompletedActivities: state.LastCompletedActivities,
		RolledBack:          state.RolledBack,
		RollbackReason:      state.RollbackReason,
	}
}

func continueCleanupAsNewIfSuggested(ctx workflow.Context, deploymentName, subredditName string, state *models.CleanupState) error {
	if !workflow.GetInfo(ctx).GetContinueAsNewSuggested() {
		return nil
	}
	workflow.GetLogger(ctx).Info("Continuing deployment cleanup as new", "deployment", deploymentName)
	_ = workflow.UpsertTypedSearchAttributes(ctx, models.RedditSubreddit.ValueSet(subredditName))
	return workflow.NewContinueAsNewErrorWithOptions(ctx, workflow.ContinueAsNewErrorOptions{
		InitialVersioningBehavior: workflow.ContinueAsNewVersioningBehaviorAutoUpgrade,
	}, DeploymentCleanupWorkflow, deploymentName, subredditName, state)
}

func healthCheckInterval(hc *models.HealthCheck) time.Duration {
	if hc == nil {
		return checkInterval
	}
	sec := hc.CheckIntervalSeconds
	if sec < 5 {
		sec = 5
	}
	return time.Duration(sec) * time.Second
}

func monitorTimedOut(ctx workflow.Context, state *models.CleanupState) bool {
	return state.MonitorDeadline != nil && !workflow.Now(ctx).Before(*state.MonitorDeadline)
}

func ensureMonitorWindow(ctx workflow.Context, state *models.CleanupState) {
	if state.HealthCheck == nil || state.MonitorStartedAt != nil {
		return
	}
	now := workflow.Now(ctx)
	state.MonitorStartedAt = &now
	if state.HealthCheck.MaxMonitorSeconds > 0 {
		deadline := now.Add(time.Duration(state.HealthCheck.MaxMonitorSeconds) * time.Second)
		state.MonitorDeadline = &deadline
	}
	workflow.GetLogger(ctx).Info("Monitoring build until success criteria pass",
		"build", state.HealthCheck.BuildID, "deadline", state.MonitorDeadline)
}

func rollbackIfUnhealthy(
	ctx workflow.Context,
	deploymentName, subredditName, currentBuildID string,
	containers []models.DockerContainerState,
	state *models.CleanupState,
) (bool, error) {
	if state.HealthCheck == nil || state.RolledBack {
		return false, nil
	}
	ensureMonitorWindow(ctx, state)
	healthCheck := state.HealthCheck
	container := findCurrentContainer(containers, healthCheck.ContainerName, currentBuildID)

	reason, err := healthFailureReason(ctx, deploymentName, subredditName, currentBuildID, container, healthCheck, state)
	if err != nil {
		return false, err
	}
	if reason == nil {
		passed, err := successCriteriaPassed(ctx, deploymentName, subredditName, currentBuildID, healthCheck, state)
		if err != nil {
			return false, err
		}
		state.HealthPassed = passed
		if state.HealthPassed {
			workflow.GetLogger(ctx).Info("Build passed deployment health checks",
				"build", currentBuildID,
				"workflows", state.LastCompletedWorkflows,
				"activities", state.LastCompletedActivities)
			return false, nil
		}
		if monitorTimedOut(ctx, state) {
			r := fmt.Sprintf(
				"timed out waiting for deployment success: workflows=%d/%d, activities=%g/%d",
				state.LastCompletedWorkflows, healthCheck.RequiredCompletedWorkflows,
				state.LastCompletedActivities, healthCheck.RequiredCompletedActivities,
			)
			reason = &r
		} else {
			return false, nil
		}
	}
	if reason == nil {
		return false, nil
	}

	state.RollbackReason = reason
	workflow.GetLogger(ctx).Error("Rolling back", "deployment", deploymentName, "reason", *reason)

	if healthCheck.PreviousBuildID == nil || *healthCheck.PreviousBuildID == "" {
		workflow.GetLogger(ctx).Error("No previous build is known; cannot roll back")
		state.RolledBack = true
		return true, nil
	}

	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 60 * time.Second,
		RetryPolicy:         deploymentRetry,
		Summary:             deploymentName + "->" + *healthCheck.PreviousBuildID,
	}
	var result models.RollbackResult
	if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "rollback_worker_deployment",
		deploymentName, *healthCheck.PreviousBuildID, container,
	).Get(ctx, &result); err != nil {
		return false, err
	}
	state.RolledBack = true
	workflow.GetLogger(ctx).Error("Rolled back deployment",
		"deployment", result.DeploymentName,
		"to", result.RollbackBuildID,
		"container_removed", result.ContainerRemoved)
	return true, nil
}

func healthFailureReason(
	ctx workflow.Context,
	deploymentName, subredditName, currentBuildID string,
	container *models.DockerContainerState,
	healthCheck *models.HealthCheck,
	state *models.CleanupState,
) (*string, error) {
	if container == nil {
		r := fmt.Sprintf("container for build %s is missing", currentBuildID)
		return &r, nil
	}
	stateStr := ""
	if container.State != nil {
		stateStr = *container.State
	}
	if stateStr != "running" {
		status := "no status"
		if container.Status != nil {
			status = *container.Status
		}
		if stateStr == "" {
			stateStr = "unknown"
		}
		r := fmt.Sprintf("container %s is %s (%s)", container.Name, stateStr, status)
		return &r, nil
	}

	sinceTime := workflow.Now(ctx)
	if state.MonitorStartedAt != nil {
		sinceTime = *state.MonitorStartedAt
	}
	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 30 * time.Second,
		RetryPolicy:         deploymentRetry,
		Summary:             currentBuildID,
	}
	var failureSummary models.TemporalExecutionSummary
	err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "count_failed_workflows_for_deployment",
		deploymentName, currentBuildID, temporalQueryTime(sinceTime),
	).Get(ctx, &failureSummary)
	if err != nil {
		workflow.GetLogger(ctx).Warn("Temporal failure health check failed", "error", err)
		failureSummary = models.TemporalExecutionSummary{}
	}
	if failureSummary.Count > healthCheck.MaxFailedWorkflows {
		r := fmt.Sprintf("%d failed workflows for build %s: %v",
			failureSummary.Count, currentBuildID, failureSummary.WorkflowIDs)
		return &r, nil
	}

	return metricsFailureReason(ctx, healthCheck, subredditName, state)
}

func successCriteriaPassed(
	ctx workflow.Context,
	deploymentName, subredditName, currentBuildID string,
	healthCheck *models.HealthCheck,
	state *models.CleanupState,
) (bool, error) {
	sinceTime := workflow.Now(ctx)
	if state.MonitorStartedAt != nil {
		sinceTime = *state.MonitorStartedAt
	}

	if healthCheck.RequiredCompletedWorkflows > 0 {
		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         deploymentRetry,
			Summary:             currentBuildID,
		}
		var completed models.TemporalExecutionSummary
		err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "count_completed_workflows_for_deployment",
			deploymentName, currentBuildID, temporalQueryTime(sinceTime),
		).Get(ctx, &completed)
		if err != nil {
			workflow.GetLogger(ctx).Warn("Temporal completion health check failed", "error", err)
			state.LastCompletedWorkflows = 0
		} else {
			state.LastCompletedWorkflows = completed.Count
		}
		if state.LastCompletedWorkflows < healthCheck.RequiredCompletedWorkflows {
			return false, nil
		}
	}

	if healthCheck.RequiredCompletedActivities <= 0 {
		state.LastCompletedActivities = 0
		return true, nil
	}

	delta, err := metricsDelta(ctx, healthCheck, subredditName, state)
	if err != nil {
		workflow.GetLogger(ctx).Warn("SDK success metrics unavailable", "error", err)
		return false, nil
	}
	if delta == nil {
		return false, nil
	}
	state.LastCompletedActivities = delta.ActivityCompleted
	return delta.ActivityCompleted >= float64(healthCheck.RequiredCompletedActivities), nil
}

func metricsFailureReason(
	ctx workflow.Context,
	healthCheck *models.HealthCheck,
	subredditName string,
	state *models.CleanupState,
) (*string, error) {
	delta, err := metricsDelta(ctx, healthCheck, subredditName, state)
	if err != nil {
		if healthCheck.RequireMetrics {
			r := fmt.Sprintf("SDK metrics unavailable: %v", err)
			return &r, nil
		}
		return nil, nil
	}
	if delta == nil {
		return nil, nil
	}
	if delta.WorkflowFailed > float64(healthCheck.MaxSDKWorkflowFailures) {
		r := fmt.Sprintf("SDK workflow failures increased by %g", delta.WorkflowFailed)
		return &r, nil
	}
	if delta.ActivityFailed > float64(healthCheck.MaxSDKActivityFailures) {
		r := fmt.Sprintf("SDK activity failures increased by %g", delta.ActivityFailed)
		return &r, nil
	}
	if delta.WorkflowTaskFailed > float64(healthCheck.MaxSDKWorkflowTaskFailures) {
		r := fmt.Sprintf("SDK workflow task failures increased by %g", delta.WorkflowTaskFailed)
		return &r, nil
	}
	return nil, nil
}

func metricsDelta(
	ctx workflow.Context,
	healthCheck *models.HealthCheck,
	subredditName string,
	state *models.CleanupState,
) (*models.SDKMetricsSnapshot, error) {
	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 15 * time.Second,
		RetryPolicy:         deploymentRetry,
		Summary:             healthCheck.BuildID,
	}
	var current models.SDKMetricsSnapshot
	err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "collect_sdk_metrics", healthCheck, subredditName).Get(ctx, &current)
	if err != nil {
		if healthCheck.RequireMetrics {
			return nil, err
		}
		workflow.GetLogger(ctx).Info("SDK metrics unavailable; skipping check", "error", err)
		return nil, nil
	}
	if state.BaselineMetrics == nil {
		state.BaselineMetrics = &current
		empty := models.SDKMetricsSnapshot{}
		return &empty, nil
	}
	delta := current.DeltaFrom(*state.BaselineMetrics)
	return &delta, nil
}

func findCurrentContainer(containers []models.DockerContainerState, containerName *string, buildID string) *models.DockerContainerState {
	if containerName != nil {
		for i := range containers {
			if containers[i].Name == *containerName {
				return &containers[i]
			}
		}
	}
	for i := range containers {
		if containers[i].BuildID == buildID {
			return &containers[i]
		}
	}
	return nil
}

func temporalQueryTime(value time.Time) string {
	return value.UTC().Format("2006-01-02T15:04:05.000000000Z")
}
