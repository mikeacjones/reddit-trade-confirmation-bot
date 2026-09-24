package workflows

import (
	"fmt"
	"time"

	"go.temporal.io/sdk/workflow"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/deployment"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/searchattr"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/shared"
)

var checkInterval = 30 * time.Second

// DeploymentCleanupWorkflow monitors Worker Deployment drainage and removes old Docker containers.
func DeploymentCleanupWorkflow(ctx workflow.Context, deploymentName, subredditName string, state *deployment.CleanupState) (map[string]any, error) {
	if state == nil {
		state = &deployment.CleanupState{}
	}

	workflow.Go(ctx, func(ctx workflow.Context) {
		ch := workflow.GetSignalChannel(ctx, "deployed")
		for {
			var signal deployment.DeployedSignal
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
				hc := deployment.HealthCheck{BuildID: buildID}
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
	_ = workflow.SetQueryHandler(ctx, "get_status", func() (map[string]any, error) {
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
			return nil, err
		}

		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         shared.DeploymentRetry,
			Summary:             deploymentName,
		}
		var depState deployment.WorkerDeploymentState
		err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "describe_worker_deployment", deploymentName).Get(ctx, &depState)
		if err != nil {
			workflow.GetLogger(ctx).Warn("Deployment inspection failed", "error", err)
			if err2 := continueCleanupAsNewIfSuggested(ctx, deploymentName, subredditName, state); err2 != nil {
				return nil, err2
			}
			_ = workflow.Sleep(ctx, checkInterval)
			continue
		}

		cao := workflow.ActivityOptions{
			StartToCloseTimeout: 15 * time.Second,
			RetryPolicy:         shared.DeploymentRetry,
			Summary:             deploymentName,
		}
		var containers []deployment.DockerContainerState
		err = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, cao), "list_deployment_containers", deploymentName).Get(ctx, &containers)
		if err != nil {
			workflow.GetLogger(ctx).Warn("Deployment inspection failed", "error", err)
			if err2 := continueCleanupAsNewIfSuggested(ctx, deploymentName, subredditName, state); err2 != nil {
				return nil, err2
			}
			_ = workflow.Sleep(ctx, checkInterval)
			continue
		}

		var activeVersions []deployment.WorkerVersionState
		containersByBuild := map[string]deployment.DockerContainerState{}
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
			return nil, err
		}

		rolledBack, err := rollbackIfUnhealthy(ctx, deploymentName, subredditName, *currentBuildID, containers, state)
		if err != nil {
			return nil, err
		}
		if rolledBack {
			return cleanupStatus(deploymentName, subredditName, state), nil
		}

		if state.HealthCheck != nil && !state.HealthPassed {
			if err := continueCleanupAsNewIfSuggested(ctx, deploymentName, subredditName, state); err != nil {
				return nil, err
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
				RetryPolicy:         shared.DeploymentRetry,
				Summary:             container.Name,
			}
			var result deployment.DockerCleanupResult
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
			return nil, err
		}
		_ = workflow.Sleep(ctx, checkInterval)
	}
}

func cleanupStatus(deploymentName, subredditName string, state *deployment.CleanupState) map[string]any {
	return map[string]any{
		"current_build_id":     state.CurrentBuildID,
		"seen_build_ids":       state.SeenBuildIDs,
		"deployment_name":      deploymentName,
		"subreddit_name":       subredditName,
		"active_build_ids":     state.LastActiveBuildIDs,
		"container_build_ids":  state.LastContainerBuildIDs,
		"cleanup_count":        state.CleanupCount,
		"monitor_started_at":   state.MonitorStartedAt,
		"monitor_deadline":     state.MonitorDeadline,
		"health_passed":        state.HealthPassed,
		"completed_workflows":  state.LastCompletedWorkflows,
		"completed_activities": state.LastCompletedActivities,
		"rolled_back":          state.RolledBack,
		"rollback_reason":      state.RollbackReason,
	}
}

func continueCleanupAsNewIfSuggested(ctx workflow.Context, deploymentName, subredditName string, state *deployment.CleanupState) error {
	if !workflow.GetInfo(ctx).GetContinueAsNewSuggested() {
		return nil
	}
	workflow.GetLogger(ctx).Info("Continuing deployment cleanup as new", "deployment", deploymentName)
	_ = workflow.UpsertTypedSearchAttributes(ctx, searchattr.RedditSubreddit.ValueSet(subredditName))
	return workflow.NewContinueAsNewErrorWithOptions(ctx, workflow.ContinueAsNewErrorOptions{
		InitialVersioningBehavior: workflow.ContinueAsNewVersioningBehaviorAutoUpgrade,
	}, DeploymentCleanupWorkflow, deploymentName, subredditName, state)
}

func healthCheckInterval(hc *deployment.HealthCheck) time.Duration {
	if hc == nil {
		return checkInterval
	}
	sec := hc.CheckIntervalSeconds
	if sec < 5 {
		sec = 5
	}
	return time.Duration(sec) * time.Second
}

func monitorTimedOut(ctx workflow.Context, state *deployment.CleanupState) bool {
	return state.MonitorDeadline != nil && !workflow.Now(ctx).Before(*state.MonitorDeadline)
}

func ensureMonitorWindow(ctx workflow.Context, state *deployment.CleanupState) {
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
	containers []deployment.DockerContainerState,
	state *deployment.CleanupState,
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
		RetryPolicy:         shared.DeploymentRetry,
		Summary:             deploymentName + "->" + *healthCheck.PreviousBuildID,
	}
	var result deployment.RollbackResult
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
	container *deployment.DockerContainerState,
	healthCheck *deployment.HealthCheck,
	state *deployment.CleanupState,
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
		RetryPolicy:         shared.DeploymentRetry,
		Summary:             currentBuildID,
	}
	var failureSummary deployment.TemporalExecutionSummary
	err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "count_failed_workflows_for_deployment",
		deploymentName, currentBuildID, temporalQueryTime(sinceTime),
	).Get(ctx, &failureSummary)
	if err != nil {
		workflow.GetLogger(ctx).Warn("Temporal failure health check failed", "error", err)
		failureSummary = deployment.TemporalExecutionSummary{}
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
	healthCheck *deployment.HealthCheck,
	state *deployment.CleanupState,
) (bool, error) {
	sinceTime := workflow.Now(ctx)
	if state.MonitorStartedAt != nil {
		sinceTime = *state.MonitorStartedAt
	}

	if healthCheck.RequiredCompletedWorkflows > 0 {
		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         shared.DeploymentRetry,
			Summary:             currentBuildID,
		}
		var completed deployment.TemporalExecutionSummary
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
	healthCheck *deployment.HealthCheck,
	subredditName string,
	state *deployment.CleanupState,
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
	healthCheck *deployment.HealthCheck,
	subredditName string,
	state *deployment.CleanupState,
) (*deployment.SDKMetricsSnapshot, error) {
	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 15 * time.Second,
		RetryPolicy:         shared.DeploymentRetry,
		Summary:             healthCheck.BuildID,
	}
	var current deployment.SDKMetricsSnapshot
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
		empty := deployment.SDKMetricsSnapshot{}
		return &empty, nil
	}
	delta := current.DeltaFrom(*state.BaselineMetrics)
	return &delta, nil
}

func findCurrentContainer(containers []deployment.DockerContainerState, containerName *string, buildID string) *deployment.DockerContainerState {
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
