"""Serializable models for worker deployment cleanup."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DeploymentHealthCheck:
    """Health-check settings for a newly deployed build."""

    build_id: str
    previous_build_id: str | None = None
    container_name: str | None = None
    max_monitor_seconds: int = 0
    check_interval_seconds: int = 60
    metrics_url: str | None = None
    require_metrics: bool = False
    required_completed_workflows: int = 5
    required_completed_activities: int = 5
    max_failed_workflows: int = 0
    max_sdk_workflow_failures: int = 0
    max_sdk_activity_failures: int = 0
    max_sdk_workflow_task_failures: int = 0


@dataclass
class TemporalExecutionSummary:
    """Recent Temporal workflow executions for a Worker Deployment Version."""

    count: int = 0
    workflow_ids: list[str] = field(default_factory=list)


@dataclass
class SdkMetricsSnapshot:
    """Relevant Temporal SDK metric counters for deployment health."""

    workflow_completed: float = 0
    activity_completed: float = 0
    workflow_failed: float = 0
    activity_failed: float = 0
    workflow_task_failed: float = 0

    def delta_from(self, baseline: "SdkMetricsSnapshot") -> "SdkMetricsSnapshot":
        return SdkMetricsSnapshot(
            workflow_completed=max(
                0,
                self.workflow_completed - baseline.workflow_completed,
            ),
            activity_completed=max(
                0,
                self.activity_completed - baseline.activity_completed,
            ),
            workflow_failed=max(0, self.workflow_failed - baseline.workflow_failed),
            activity_failed=max(0, self.activity_failed - baseline.activity_failed),
            workflow_task_failed=max(
                0,
                self.workflow_task_failed - baseline.workflow_task_failed,
            ),
        )


@dataclass
class DeploymentCleanupState:
    """State carried across cleanup workflow continue-as-new runs."""

    current_build_id: str | None = None
    seen_build_ids: list[str] = field(default_factory=list)
    last_active_build_ids: list[str] = field(default_factory=list)
    last_container_build_ids: list[str] = field(default_factory=list)
    cleanup_count: int = 0
    health_check: DeploymentHealthCheck | None = None
    monitor_started_at: datetime | None = None
    monitor_deadline: datetime | None = None
    baseline_metrics: SdkMetricsSnapshot | None = None
    health_passed: bool = False
    last_completed_workflows: int = 0
    last_completed_activities: float = 0.0
    rolled_back: bool = False
    rollback_reason: str | None = None


@dataclass
class DeploymentRollbackResult:
    """Result of rolling a deployment back."""

    deployment_name: str
    rollback_build_id: str
    container_removed: bool = False


@dataclass
class WorkerDeploymentVersionState:
    """Temporal Worker Deployment Version state needed for cleanup."""

    build_id: str
    status: str
    drainage_status: str | None = None

    @property
    def is_drained(self) -> bool:
        return self.status == "DRAINED" or self.drainage_status == "DRAINED"

    @property
    def is_cleanup_ready(self) -> bool:
        return self.is_drained or self.status == "INACTIVE"

    @property
    def is_active(self) -> bool:
        return self.status not in {"DRAINED", "INACTIVE"}


@dataclass
class WorkerDeploymentState:
    """Temporal Worker Deployment state."""

    deployment_name: str
    versions: list[WorkerDeploymentVersionState] = field(default_factory=list)


@dataclass
class DockerContainerState:
    """Docker container associated with a Worker Deployment Version."""

    id: str
    name: str
    build_id: str
    image: str | None = None
    state: str | None = None
    status: str | None = None


@dataclass
class DockerCleanupResult:
    """Result of deleting a drained deployment container."""

    container_name: str
    container_removed: bool = False
    image_removed: bool = False
    pruned_containers: int = 0
    image_remove_error: str | None = None
