package models

import "time"

// DeployedSignal is the payload for the deployment cleanup "deployed" signal.
type DeployedSignal struct {
	BuildID     string       `json:"build_id"`
	HealthCheck *HealthCheck `json:"health_check"`
}

// HealthCheck settings for a newly deployed build.
type HealthCheck struct {
	BuildID                     string  `json:"build_id"`
	PreviousBuildID             *string `json:"previous_build_id,omitempty"`
	ContainerName               *string `json:"container_name,omitempty"`
	MaxMonitorSeconds           int     `json:"max_monitor_seconds"`
	CheckIntervalSeconds        int     `json:"check_interval_seconds"`
	MetricsURL                  *string `json:"metrics_url,omitempty"`
	RequireMetrics              bool    `json:"require_metrics"`
	RequiredCompletedWorkflows  int     `json:"required_completed_workflows"`
	RequiredCompletedActivities int     `json:"required_completed_activities"`
	MaxFailedWorkflows          int     `json:"max_failed_workflows"`
	MaxSDKWorkflowFailures      int     `json:"max_sdk_workflow_failures"`
	MaxSDKActivityFailures      int     `json:"max_sdk_activity_failures"`
	MaxSDKWorkflowTaskFailures  int     `json:"max_sdk_workflow_task_failures"`
}

// TemporalExecutionSummary counts recent Temporal workflow executions for a version.
type TemporalExecutionSummary struct {
	Count       int      `json:"count"`
	WorkflowIDs []string `json:"workflow_ids"`
}

// SDKMetricsSnapshot holds Temporal SDK metric counters for deployment health.
type SDKMetricsSnapshot struct {
	WorkflowCompleted  float64 `json:"workflow_completed"`
	ActivityCompleted  float64 `json:"activity_completed"`
	WorkflowFailed     float64 `json:"workflow_failed"`
	ActivityFailed     float64 `json:"activity_failed"`
	WorkflowTaskFailed float64 `json:"workflow_task_failed"`
}

// DeltaFrom returns non-negative deltas from a baseline snapshot.
func (s SDKMetricsSnapshot) DeltaFrom(baseline SDKMetricsSnapshot) SDKMetricsSnapshot {
	return SDKMetricsSnapshot{
		WorkflowCompleted:  max(0, s.WorkflowCompleted-baseline.WorkflowCompleted),
		ActivityCompleted:  max(0, s.ActivityCompleted-baseline.ActivityCompleted),
		WorkflowFailed:     max(0, s.WorkflowFailed-baseline.WorkflowFailed),
		ActivityFailed:     max(0, s.ActivityFailed-baseline.ActivityFailed),
		WorkflowTaskFailed: max(0, s.WorkflowTaskFailed-baseline.WorkflowTaskFailed),
	}
}

// CleanupState is carried across cleanup workflow continue-as-new runs.
type CleanupState struct {
	CurrentBuildID          *string             `json:"current_build_id,omitempty"`
	SeenBuildIDs            []string            `json:"seen_build_ids"`
	LastActiveBuildIDs      []string            `json:"last_active_build_ids"`
	LastContainerBuildIDs   []string            `json:"last_container_build_ids"`
	CleanupCount            int                 `json:"cleanup_count"`
	HealthCheck             *HealthCheck        `json:"health_check,omitempty"`
	MonitorStartedAt        *time.Time          `json:"monitor_started_at,omitempty"`
	MonitorDeadline         *time.Time          `json:"monitor_deadline,omitempty"`
	BaselineMetrics         *SDKMetricsSnapshot `json:"baseline_metrics,omitempty"`
	HealthPassed            bool                `json:"health_passed"`
	LastCompletedWorkflows  int                 `json:"last_completed_workflows"`
	LastCompletedActivities float64             `json:"last_completed_activities"`
	RolledBack              bool                `json:"rolled_back"`
	RollbackReason          *string             `json:"rollback_reason,omitempty"`
}

// CleanupStatus is the deployment-cleanup workflow result / get_status query.
type CleanupStatus struct {
	CurrentBuildID      *string    `json:"current_build_id"`
	SeenBuildIDs        []string   `json:"seen_build_ids"`
	DeploymentName      string     `json:"deployment_name"`
	SubredditName       string     `json:"subreddit_name"`
	ActiveBuildIDs      []string   `json:"active_build_ids"`
	ContainerBuildIDs   []string   `json:"container_build_ids"`
	CleanupCount        int        `json:"cleanup_count"`
	MonitorStartedAt    *time.Time `json:"monitor_started_at"`
	MonitorDeadline     *time.Time `json:"monitor_deadline"`
	HealthPassed        bool       `json:"health_passed"`
	CompletedWorkflows  int        `json:"completed_workflows"`
	CompletedActivities float64    `json:"completed_activities"`
	RolledBack          bool       `json:"rolled_back"`
	RollbackReason      *string    `json:"rollback_reason"`
}

// RollbackResult is the result of rolling a deployment back.
type RollbackResult struct {
	DeploymentName   string `json:"deployment_name"`
	RollbackBuildID  string `json:"rollback_build_id"`
	ContainerRemoved bool   `json:"container_removed"`
}

// WorkerVersionState is Temporal Worker Deployment Version state for cleanup.
type WorkerVersionState struct {
	BuildID        string  `json:"build_id"`
	Status         string  `json:"status"`
	DrainageStatus *string `json:"drainage_status,omitempty"`
}

func (v WorkerVersionState) IsDrained() bool {
	return v.Status == "DRAINED" || (v.DrainageStatus != nil && *v.DrainageStatus == "DRAINED")
}

func (v WorkerVersionState) IsCleanupReady() bool {
	return v.IsDrained() || v.Status == "INACTIVE"
}

func (v WorkerVersionState) IsActive() bool {
	return v.Status != "DRAINED" && v.Status != "INACTIVE"
}

// WorkerDeploymentState is Temporal Worker Deployment state.
type WorkerDeploymentState struct {
	DeploymentName string               `json:"deployment_name"`
	Versions       []WorkerVersionState `json:"versions"`
}

// DockerContainerState is a Docker container associated with a deployment version.
type DockerContainerState struct {
	ID      string  `json:"id"`
	Name    string  `json:"name"`
	BuildID string  `json:"build_id"`
	Image   *string `json:"image,omitempty"`
	State   *string `json:"state,omitempty"`
	Status  *string `json:"status,omitempty"`
}

// DockerCleanupResult is the result of deleting a drained deployment container.
type DockerCleanupResult struct {
	ContainerName    string  `json:"container_name"`
	ContainerRemoved bool    `json:"container_removed"`
	ImageRemoved     bool    `json:"image_removed"`
	PrunedContainers int     `json:"pruned_containers"`
	ImageRemoveError *string `json:"image_remove_error,omitempty"`
}
