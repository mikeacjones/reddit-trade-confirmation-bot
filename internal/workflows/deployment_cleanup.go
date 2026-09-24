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

type cleanupRuntime struct {
	currentBuildID          *string
	seenBuildIDs            []string
	deploymentName          string
	subredditName           string
	lastActiveBuildIDs      []string
	lastContainerBuildIDs   []string
	cleanupCount            int
	healthCheck             *deployment.HealthCheck
	monitorStartedAt        *time.Time
	monitorDeadline         *time.Time
	baselineMetrics         *deployment.SDKMetricsSnapshot
	healthPassed            bool
	lastCompletedWorkflows  int
	lastCompletedActivities float64
	rolledBack              bool
	rollbackReason          *string
}

// DeploymentCleanupWorkflow monitors Worker Deployment drainage and removes old Docker containers.
func DeploymentCleanupWorkflow(ctx workflow.Context, deploymentName, subredditName string, state *deployment.CleanupState) (map[string]any, error) {
	rt := &cleanupRuntime{
		deploymentName: deploymentName,
		subredditName:  subredditName,
	}
	if state != nil {
		rt.restore(state)
	}

	workflow.Go(ctx, func(ctx workflow.Context) {
		ch := workflow.GetSignalChannel(ctx, "deployed")
		for {
			var signal struct {
				BuildID     string                  `json:"build_id"`
				HealthCheck *deployment.HealthCheck `json:"health_check"`
			}
			ch.Receive(ctx, &signal)
			buildID := signal.BuildID
			found := false
			for _, id := range rt.seenBuildIDs {
				if id == buildID {
					found = true
					break
				}
			}
			if !found {
				rt.seenBuildIDs = append(rt.seenBuildIDs, buildID)
			}
			rt.currentBuildID = &buildID
			if signal.HealthCheck == nil {
				hc := deployment.HealthCheck{BuildID: buildID}
				rt.healthCheck = &hc
			} else {
				rt.healthCheck = signal.HealthCheck
			}
			rt.monitorStartedAt = nil
			rt.monitorDeadline = nil
			rt.baselineMetrics = nil
			rt.healthPassed = false
			rt.lastCompletedWorkflows = 0
			rt.lastCompletedActivities = 0
			rt.rolledBack = false
			rt.rollbackReason = nil
		}
	})
	_ = workflow.SetQueryHandler(ctx, "get_status", func() (map[string]any, error) {
		return rt.status(), nil
	})

	_ = workflow.Await(ctx, func() bool { return rt.currentBuildID != nil })

	for {
		currentBuildID := rt.currentBuildID
		if currentBuildID == nil {
			_ = workflow.Sleep(ctx, checkInterval)
			continue
		}

		if err := rt.continueAsNewIfSuggested(ctx, deploymentName, subredditName); err != nil {
			return nil, err
		}

		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         shared.DeploymentRetryPolicy(),
			Summary:             deploymentName,
		}
		var depState deployment.WorkerDeploymentState
		err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "describe_worker_deployment", deploymentName).Get(ctx, &depState)
		if err != nil {
			workflow.GetLogger(ctx).Warn("Deployment inspection failed", "error", err)
			if err2 := rt.continueAsNewIfSuggested(ctx, deploymentName, subredditName); err2 != nil {
				return nil, err2
			}
			_ = workflow.Sleep(ctx, checkInterval)
			continue
		}

		cao := workflow.ActivityOptions{
			StartToCloseTimeout: 15 * time.Second,
			RetryPolicy:         shared.DeploymentRetryPolicy(),
			Summary:             deploymentName,
		}
		var containers []deployment.DockerContainerState
		err = workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, cao), "list_deployment_containers", deploymentName).Get(ctx, &containers)
		if err != nil {
			workflow.GetLogger(ctx).Warn("Deployment inspection failed", "error", err)
			if err2 := rt.continueAsNewIfSuggested(ctx, deploymentName, subredditName); err2 != nil {
				return nil, err2
			}
			_ = workflow.Sleep(ctx, checkInterval)
			continue
		}

		var activeVersions []deployment.WorkerVersionState
		containersByBuild := map[string]deployment.DockerContainerState{}
		rt.lastActiveBuildIDs = nil
		rt.lastContainerBuildIDs = nil
		for _, v := range depState.Versions {
			if v.IsActive() {
				activeVersions = append(activeVersions, v)
				rt.lastActiveBuildIDs = append(rt.lastActiveBuildIDs, v.BuildID)
			}
		}
		for _, c := range containers {
			containersByBuild[c.BuildID] = c
			rt.lastContainerBuildIDs = append(rt.lastContainerBuildIDs, c.BuildID)
		}

		if err := rt.continueAsNewIfSuggested(ctx, deploymentName, subredditName); err != nil {
			return nil, err
		}

		rolledBack, err := rt.rollbackIfUnhealthy(ctx, deploymentName, subredditName, *currentBuildID, containers)
		if err != nil {
			return nil, err
		}
		if rolledBack {
			return rt.status(), nil
		}

		if rt.healthCheck != nil && !rt.healthPassed {
			if err := rt.continueAsNewIfSuggested(ctx, deploymentName, subredditName); err != nil {
				return nil, err
			}
			_ = workflow.Sleep(ctx, rt.checkInterval())
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
				RetryPolicy:         shared.DeploymentRetryPolicy(),
				Summary:             container.Name,
			}
			var result deployment.DockerCleanupResult
			err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, rao), "remove_deployment_container", container).Get(ctx, &result)
			if err != nil {
				workflow.GetLogger(ctx).Warn("Failed to remove drained container", "build", version.BuildID, "error", err)
				cleanupFailed = true
				continue
			}
			rt.cleanupCount++
			if result.ImageRemoveError != nil {
				workflow.GetLogger(ctx).Warn("Removed container but could not remove image",
					"container", result.ContainerName, "error", *result.ImageRemoveError)
			} else {
				workflow.GetLogger(ctx).Info("Removed drained deployment container", "container", result.ContainerName)
			}
		}

		if len(activeVersions) <= 1 && !cleanupFailed {
			workflow.GetLogger(ctx).Info("Deployment cleanup complete",
				"deployment", deploymentName, "active", rt.lastActiveBuildIDs)
			return rt.status(), nil
		}

		if err := rt.continueAsNewIfSuggested(ctx, deploymentName, subredditName); err != nil {
			return nil, err
		}
		_ = workflow.Sleep(ctx, checkInterval)
	}
}

func (rt *cleanupRuntime) status() map[string]any {
	return map[string]any{
		"current_build_id":     rt.currentBuildID,
		"seen_build_ids":       rt.seenBuildIDs,
		"deployment_name":      rt.deploymentName,
		"subreddit_name":       rt.subredditName,
		"active_build_ids":     rt.lastActiveBuildIDs,
		"container_build_ids":  rt.lastContainerBuildIDs,
		"cleanup_count":        rt.cleanupCount,
		"monitor_started_at":   rt.monitorStartedAt,
		"monitor_deadline":     rt.monitorDeadline,
		"health_passed":        rt.healthPassed,
		"completed_workflows":  rt.lastCompletedWorkflows,
		"completed_activities": rt.lastCompletedActivities,
		"rolled_back":          rt.rolledBack,
		"rollback_reason":      rt.rollbackReason,
	}
}

func (rt *cleanupRuntime) restore(state *deployment.CleanupState) {
	rt.currentBuildID = state.CurrentBuildID
	rt.seenBuildIDs = append([]string{}, state.SeenBuildIDs...)
	rt.lastActiveBuildIDs = append([]string{}, state.LastActiveBuildIDs...)
	rt.lastContainerBuildIDs = append([]string{}, state.LastContainerBuildIDs...)
	rt.cleanupCount = state.CleanupCount
	rt.healthCheck = state.HealthCheck
	rt.monitorStartedAt = state.MonitorStartedAt
	rt.monitorDeadline = state.MonitorDeadline
	rt.baselineMetrics = state.BaselineMetrics
	rt.healthPassed = state.HealthPassed
	rt.lastCompletedWorkflows = state.LastCompletedWorkflows
	rt.lastCompletedActivities = state.LastCompletedActivities
	rt.rolledBack = state.RolledBack
	rt.rollbackReason = state.RollbackReason
}

func (rt *cleanupRuntime) snapshot() *deployment.CleanupState {
	return &deployment.CleanupState{
		CurrentBuildID:          rt.currentBuildID,
		SeenBuildIDs:            append([]string{}, rt.seenBuildIDs...),
		LastActiveBuildIDs:      append([]string{}, rt.lastActiveBuildIDs...),
		LastContainerBuildIDs:   append([]string{}, rt.lastContainerBuildIDs...),
		CleanupCount:            rt.cleanupCount,
		HealthCheck:             rt.healthCheck,
		MonitorStartedAt:        rt.monitorStartedAt,
		MonitorDeadline:         rt.monitorDeadline,
		BaselineMetrics:         rt.baselineMetrics,
		HealthPassed:            rt.healthPassed,
		LastCompletedWorkflows:  rt.lastCompletedWorkflows,
		LastCompletedActivities: rt.lastCompletedActivities,
		RolledBack:              rt.rolledBack,
		RollbackReason:          rt.rollbackReason,
	}
}

func (rt *cleanupRuntime) continueAsNewIfSuggested(ctx workflow.Context, deploymentName, subredditName string) error {
	if !workflow.GetInfo(ctx).GetContinueAsNewSuggested() {
		return nil
	}
	workflow.GetLogger(ctx).Info("Continuing deployment cleanup as new", "deployment", deploymentName)
	_ = workflow.UpsertTypedSearchAttributes(ctx, searchattr.RedditSubreddit.ValueSet(subredditName))
	return workflow.NewContinueAsNewErrorWithOptions(ctx, workflow.ContinueAsNewErrorOptions{
		InitialVersioningBehavior: workflow.ContinueAsNewVersioningBehaviorAutoUpgrade,
	}, DeploymentCleanupWorkflow, deploymentName, subredditName, rt.snapshot())
}

func (rt *cleanupRuntime) checkInterval() time.Duration {
	if rt.healthCheck == nil {
		return checkInterval
	}
	sec := rt.healthCheck.CheckIntervalSeconds
	if sec < 5 {
		sec = 5
	}
	return time.Duration(sec) * time.Second
}

func (rt *cleanupRuntime) monitorTimedOut(ctx workflow.Context) bool {
	return rt.monitorDeadline != nil && !workflow.Now(ctx).Before(*rt.monitorDeadline)
}

func (rt *cleanupRuntime) ensureMonitorWindow(ctx workflow.Context) {
	if rt.healthCheck == nil || rt.monitorStartedAt != nil {
		return
	}
	now := workflow.Now(ctx)
	rt.monitorStartedAt = &now
	if rt.healthCheck.MaxMonitorSeconds > 0 {
		deadline := now.Add(time.Duration(rt.healthCheck.MaxMonitorSeconds) * time.Second)
		rt.monitorDeadline = &deadline
	}
	workflow.GetLogger(ctx).Info("Monitoring build until success criteria pass",
		"build", rt.healthCheck.BuildID, "deadline", rt.monitorDeadline)
}

func (rt *cleanupRuntime) rollbackIfUnhealthy(
	ctx workflow.Context,
	deploymentName, subredditName, currentBuildID string,
	containers []deployment.DockerContainerState,
) (bool, error) {
	if rt.healthCheck == nil || rt.rolledBack {
		return false, nil
	}
	rt.ensureMonitorWindow(ctx)
	healthCheck := rt.healthCheck
	container := findCurrentContainer(containers, healthCheck.ContainerName, currentBuildID)

	reason, err := rt.healthFailureReason(ctx, deploymentName, subredditName, currentBuildID, container, healthCheck)
	if err != nil {
		return false, err
	}
	if reason == nil {
		passed, err := rt.successCriteriaPassed(ctx, deploymentName, subredditName, currentBuildID, healthCheck)
		if err != nil {
			return false, err
		}
		rt.healthPassed = passed
		if rt.healthPassed {
			workflow.GetLogger(ctx).Info("Build passed deployment health checks",
				"build", currentBuildID,
				"workflows", rt.lastCompletedWorkflows,
				"activities", rt.lastCompletedActivities)
			return false, nil
		}
		if rt.monitorTimedOut(ctx) {
			r := fmt.Sprintf(
				"timed out waiting for deployment success: workflows=%d/%d, activities=%g/%d",
				rt.lastCompletedWorkflows, healthCheck.RequiredCompletedWorkflows,
				rt.lastCompletedActivities, healthCheck.RequiredCompletedActivities,
			)
			reason = &r
		} else {
			return false, nil
		}
	}
	if reason == nil {
		return false, nil
	}

	rt.rollbackReason = reason
	workflow.GetLogger(ctx).Error("Rolling back", "deployment", deploymentName, "reason", *reason)

	if healthCheck.PreviousBuildID == nil || *healthCheck.PreviousBuildID == "" {
		workflow.GetLogger(ctx).Error("No previous build is known; cannot roll back")
		rt.rolledBack = true
		return true, nil
	}

	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 60 * time.Second,
		RetryPolicy:         shared.DeploymentRetryPolicy(),
		Summary:             deploymentName + "->" + *healthCheck.PreviousBuildID,
	}
	var result deployment.RollbackResult
	if err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "rollback_worker_deployment",
		deploymentName, *healthCheck.PreviousBuildID, container,
	).Get(ctx, &result); err != nil {
		return false, err
	}
	rt.rolledBack = true
	workflow.GetLogger(ctx).Error("Rolled back deployment",
		"deployment", result.DeploymentName,
		"to", result.RollbackBuildID,
		"container_removed", result.ContainerRemoved)
	return true, nil
}

func (rt *cleanupRuntime) healthFailureReason(
	ctx workflow.Context,
	deploymentName, subredditName, currentBuildID string,
	container *deployment.DockerContainerState,
	healthCheck *deployment.HealthCheck,
) (*string, error) {
	if container == nil {
		r := fmt.Sprintf("container for build %s is missing", currentBuildID)
		return &r, nil
	}
	state := ""
	if container.State != nil {
		state = *container.State
	}
	if state != "running" {
		status := "no status"
		if container.Status != nil {
			status = *container.Status
		}
		if state == "" {
			state = "unknown"
		}
		r := fmt.Sprintf("container %s is %s (%s)", container.Name, state, status)
		return &r, nil
	}

	sinceTime := workflow.Now(ctx)
	if rt.monitorStartedAt != nil {
		sinceTime = *rt.monitorStartedAt
	}
	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 30 * time.Second,
		RetryPolicy:         shared.DeploymentRetryPolicy(),
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

	metricsReason, err := rt.metricsFailureReason(ctx, healthCheck, subredditName)
	if err != nil {
		return nil, err
	}
	return metricsReason, nil
}

func (rt *cleanupRuntime) successCriteriaPassed(
	ctx workflow.Context,
	deploymentName, subredditName, currentBuildID string,
	healthCheck *deployment.HealthCheck,
) (bool, error) {
	sinceTime := workflow.Now(ctx)
	if rt.monitorStartedAt != nil {
		sinceTime = *rt.monitorStartedAt
	}

	if healthCheck.RequiredCompletedWorkflows > 0 {
		ao := workflow.ActivityOptions{
			StartToCloseTimeout: 30 * time.Second,
			RetryPolicy:         shared.DeploymentRetryPolicy(),
			Summary:             currentBuildID,
		}
		var completed deployment.TemporalExecutionSummary
		err := workflow.ExecuteActivity(workflow.WithActivityOptions(ctx, ao), "count_completed_workflows_for_deployment",
			deploymentName, currentBuildID, temporalQueryTime(sinceTime),
		).Get(ctx, &completed)
		if err != nil {
			workflow.GetLogger(ctx).Warn("Temporal completion health check failed", "error", err)
			rt.lastCompletedWorkflows = 0
		} else {
			rt.lastCompletedWorkflows = completed.Count
		}
		if rt.lastCompletedWorkflows < healthCheck.RequiredCompletedWorkflows {
			return false, nil
		}
	}

	if healthCheck.RequiredCompletedActivities <= 0 {
		rt.lastCompletedActivities = 0
		return true, nil
	}

	delta, err := rt.metricsDelta(ctx, healthCheck, subredditName)
	if err != nil {
		workflow.GetLogger(ctx).Warn("SDK success metrics unavailable", "error", err)
		return false, nil
	}
	if delta == nil {
		return false, nil
	}
	rt.lastCompletedActivities = delta.ActivityCompleted
	return delta.ActivityCompleted >= float64(healthCheck.RequiredCompletedActivities), nil
}

func (rt *cleanupRuntime) metricsFailureReason(
	ctx workflow.Context,
	healthCheck *deployment.HealthCheck,
	subredditName string,
) (*string, error) {
	delta, err := rt.metricsDelta(ctx, healthCheck, subredditName)
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

func (rt *cleanupRuntime) metricsDelta(
	ctx workflow.Context,
	healthCheck *deployment.HealthCheck,
	subredditName string,
) (*deployment.SDKMetricsSnapshot, error) {
	ao := workflow.ActivityOptions{
		StartToCloseTimeout: 15 * time.Second,
		RetryPolicy:         shared.DeploymentRetryPolicy(),
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
	if rt.baselineMetrics == nil {
		rt.baselineMetrics = &current
		empty := deployment.SDKMetricsSnapshot{}
		return &empty, nil
	}
	delta := current.DeltaFrom(*rt.baselineMetrics)
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
