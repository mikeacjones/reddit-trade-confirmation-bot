"""Workflow that cleans up drained Docker-backed worker deployments."""

from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.common import VersioningBehavior
from temporalio.exceptions import ActivityError

from temporal.deployment_models import (
    DeploymentHealthCheck,
    DeploymentRollbackResult,
    DockerCleanupResult,
    DockerContainerState,
    SdkMetricsSnapshot,
    TemporalExecutionSummary,
    WorkerDeploymentState,
)
from temporal.shared import DEPLOYMENT_RETRY_POLICY

CHECK_INTERVAL = timedelta(seconds=30)


@workflow.defn(versioning_behavior=VersioningBehavior.UNSPECIFIED)
class DeploymentCleanupWorkflow:
    """Monitor Worker Deployment drainage and remove old Docker containers."""

    def __init__(self) -> None:
        self._current_build_id: str | None = None
        self._seen_build_ids: list[str] = []
        self._deployment_name: str | None = None
        self._subreddit_name: str | None = None
        self._last_active_build_ids: list[str] = []
        self._last_container_build_ids: list[str] = []
        self._cleanup_count = 0
        self._health_check: DeploymentHealthCheck | None = None
        self._monitor_started_at: datetime | None = None
        self._monitor_deadline: datetime | None = None
        self._baseline_metrics: SdkMetricsSnapshot | None = None
        self._health_passed = False
        self._last_completed_workflows = 0
        self._last_completed_activities = 0.0
        self._rolled_back = False
        self._rollback_reason: str | None = None

    @workflow.signal
    def deployed(
        self,
        build_id: str,
        health_check: DeploymentHealthCheck | None = None,
    ) -> None:
        """Signal that a new build was deployed for this subreddit."""
        if build_id not in self._seen_build_ids:
            self._seen_build_ids.append(build_id)
        self._current_build_id = build_id
        self._health_check = health_check or DeploymentHealthCheck(build_id=build_id)
        self._monitor_started_at = None
        self._monitor_deadline = None
        self._baseline_metrics = None
        self._health_passed = False
        self._last_completed_workflows = 0
        self._last_completed_activities = 0.0
        self._rolled_back = False
        self._rollback_reason = None

    @workflow.query
    def get_status(self) -> dict[str, object]:
        """Return the latest cleanup workflow status."""
        return {
            "current_build_id": self._current_build_id,
            "seen_build_ids": self._seen_build_ids,
            "deployment_name": self._deployment_name,
            "subreddit_name": self._subreddit_name,
            "active_build_ids": self._last_active_build_ids,
            "container_build_ids": self._last_container_build_ids,
            "cleanup_count": self._cleanup_count,
            "monitor_started_at": self._monitor_started_at,
            "monitor_deadline": self._monitor_deadline,
            "health_passed": self._health_passed,
            "completed_workflows": self._last_completed_workflows,
            "completed_activities": self._last_completed_activities,
            "rolled_back": self._rolled_back,
            "rollback_reason": self._rollback_reason,
        }

    @workflow.run
    async def run(self, deployment_name: str, subreddit_name: str) -> dict[str, object]:
        """Run cleanup until only one active deployment version remains."""
        self._deployment_name = deployment_name
        self._subreddit_name = subreddit_name

        await workflow.wait_condition(lambda: self._current_build_id is not None)

        while True:
            current_build_id = self._current_build_id
            if current_build_id is None:
                await workflow.sleep(CHECK_INTERVAL)
                continue

            try:
                deployment = await workflow.execute_activity(
                    "describe_worker_deployment",
                    args=[deployment_name],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DEPLOYMENT_RETRY_POLICY,
                    result_type=WorkerDeploymentState,
                    summary=deployment_name,
                )
                containers = await workflow.execute_activity(
                    "list_deployment_containers",
                    args=[deployment_name],
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=DEPLOYMENT_RETRY_POLICY,
                    result_type=list[DockerContainerState],
                    summary=deployment_name,
                )
            except ActivityError as err:
                workflow.logger.warning("Deployment inspection failed: %s", err)
                await workflow.sleep(CHECK_INTERVAL)
                continue

            active_versions = [
                version for version in deployment.versions if version.is_active
            ]
            containers_by_build_id = {
                container.build_id: container for container in containers
            }
            self._last_active_build_ids = [
                version.build_id for version in active_versions
            ]
            self._last_container_build_ids = [
                container.build_id for container in containers
            ]

            if await self._rollback_if_unhealthy_or_not_ready(
                deployment_name,
                subreddit_name,
                current_build_id,
                containers,
            ):
                return self.get_status()

            if self._health_check is not None and not self._health_passed:
                await workflow.sleep(self._check_interval())
                continue

            cleanup_failed = False
            for version in deployment.versions:
                if version.build_id == current_build_id or not version.is_cleanup_ready:
                    continue

                container = containers_by_build_id.get(version.build_id)
                if container is None:
                    continue

                try:
                    result = await workflow.execute_activity(
                        "remove_deployment_container",
                        args=[container],
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=DEPLOYMENT_RETRY_POLICY,
                        result_type=DockerCleanupResult,
                        summary=container.name,
                    )
                except ActivityError as err:
                    workflow.logger.warning(
                        "Failed to remove drained container for build %s: %s",
                        version.build_id,
                        err,
                    )
                    cleanup_failed = True
                    continue

                self._cleanup_count += 1
                if result.image_remove_error:
                    workflow.logger.warning(
                        "Removed %s but could not remove image: %s",
                        result.container_name,
                        result.image_remove_error,
                    )
                else:
                    workflow.logger.info(
                        "Removed drained deployment container %s",
                        result.container_name,
                    )

            if len(active_versions) <= 1 and not cleanup_failed:
                workflow.logger.info(
                    "Deployment cleanup complete for %s; active_build_ids=%s",
                    deployment_name,
                    self._last_active_build_ids,
                )
                return self.get_status()

            await workflow.sleep(CHECK_INTERVAL)

    def _check_interval(self) -> timedelta:
        if self._health_check is None:
            return CHECK_INTERVAL
        return timedelta(
            seconds=max(5, self._health_check.check_interval_seconds),
        )

    def _monitor_timed_out(self) -> bool:
        return (
            self._monitor_deadline is not None
            and workflow.now() >= self._monitor_deadline
        )

    def _ensure_monitor_window(self) -> None:
        if self._health_check is None or self._monitor_started_at is not None:
            return

        self._monitor_started_at = workflow.now()
        if self._health_check.max_monitor_seconds > 0:
            self._monitor_deadline = self._monitor_started_at + timedelta(
                seconds=self._health_check.max_monitor_seconds
            )
        workflow.logger.info(
            "Monitoring build %s until success criteria pass; deadline=%s",
            self._health_check.build_id,
            self._monitor_deadline,
        )

    async def _rollback_if_unhealthy_or_not_ready(
        self,
        deployment_name: str,
        subreddit_name: str,
        current_build_id: str,
        containers: list[DockerContainerState],
    ) -> bool:
        if self._health_check is None or self._rolled_back:
            return False

        self._ensure_monitor_window()

        health_check = self._health_check
        container = self._find_current_container(
            containers,
            health_check.container_name,
            current_build_id,
        )
        reason = await self._health_failure_reason(
            deployment_name,
            subreddit_name,
            current_build_id,
            container,
            health_check,
        )
        if reason is None:
            self._health_passed = await self._success_criteria_passed(
                deployment_name,
                subreddit_name,
                current_build_id,
                health_check,
            )
            if self._health_passed:
                workflow.logger.info(
                    (
                        "Build %s passed deployment health checks: "
                        "workflows=%s/%s, activities=%s/%s"
                    ),
                    current_build_id,
                    self._last_completed_workflows,
                    health_check.required_completed_workflows,
                    self._last_completed_activities,
                    health_check.required_completed_activities,
                )
                return False

            if self._monitor_timed_out():
                reason = (
                    "timed out waiting for deployment success: "
                    f"workflows={self._last_completed_workflows}/"
                    f"{health_check.required_completed_workflows}, "
                    f"activities={self._last_completed_activities}/"
                    f"{health_check.required_completed_activities}"
                )
            else:
                return False

        if reason is None:
            return False

        self._rollback_reason = reason
        workflow.logger.error("Rolling back %s: %s", deployment_name, reason)

        if not health_check.previous_build_id:
            workflow.logger.error("No previous build is known; cannot roll back")
            self._rolled_back = True
            return True

        result = await workflow.execute_activity(
            "rollback_worker_deployment",
            args=[deployment_name, health_check.previous_build_id, container],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=DEPLOYMENT_RETRY_POLICY,
            result_type=DeploymentRollbackResult,
            summary=f"{deployment_name}->{health_check.previous_build_id}",
        )
        self._rolled_back = True
        workflow.logger.error(
            "Rolled back %s to %s; failed_container_removed=%s",
            result.deployment_name,
            result.rollback_build_id,
            result.container_removed,
        )
        return True

    async def _health_failure_reason(
        self,
        deployment_name: str,
        subreddit_name: str,
        current_build_id: str,
        container: DockerContainerState | None,
        health_check: DeploymentHealthCheck,
    ) -> str | None:
        if container is None:
            return f"container for build {current_build_id} is missing"
        if container.state != "running":
            return (
                f"container {container.name} is {container.state or 'unknown'} "
                f"({container.status or 'no status'})"
            )

        since_time = self._monitor_started_at or workflow.now()
        try:
            failure_summary = await workflow.execute_activity(
                "count_failed_workflows_for_deployment",
                args=[
                    deployment_name,
                    current_build_id,
                    _temporal_query_time(since_time),
                ],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEPLOYMENT_RETRY_POLICY,
                result_type=TemporalExecutionSummary,
                summary=current_build_id,
            )
        except ActivityError as err:
            workflow.logger.warning("Temporal failure health check failed: %s", err)
            failure_summary = TemporalExecutionSummary()
        if failure_summary.count > health_check.max_failed_workflows:
            return (
                f"{failure_summary.count} failed workflows for build "
                f"{current_build_id}: {failure_summary.workflow_ids}"
            )

        metrics_reason = await self._metrics_failure_reason(
            health_check,
            subreddit_name,
        )
        if metrics_reason is not None:
            return metrics_reason

        return None

    async def _success_criteria_passed(
        self,
        deployment_name: str,
        subreddit_name: str,
        current_build_id: str,
        health_check: DeploymentHealthCheck,
    ) -> bool:
        since_time = self._monitor_started_at or workflow.now()

        if health_check.required_completed_workflows > 0:
            try:
                completed = await workflow.execute_activity(
                    "count_completed_workflows_for_deployment",
                    args=[
                        deployment_name,
                        current_build_id,
                        _temporal_query_time(since_time),
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DEPLOYMENT_RETRY_POLICY,
                    result_type=TemporalExecutionSummary,
                    summary=current_build_id,
                )
                self._last_completed_workflows = completed.count
            except ActivityError as err:
                workflow.logger.warning(
                    "Temporal completion health check failed: %s",
                    err,
                )
                self._last_completed_workflows = 0

            if self._last_completed_workflows < health_check.required_completed_workflows:
                return False

        if health_check.required_completed_activities <= 0:
            self._last_completed_activities = 0.0
            return True

        try:
            metrics_delta = await self._metrics_delta(health_check, subreddit_name)
        except ActivityError as err:
            workflow.logger.warning("SDK success metrics unavailable: %s", err)
            return False
        if metrics_delta is None:
            return False

        self._last_completed_activities = metrics_delta.activity_completed
        return (
            metrics_delta.activity_completed
            >= health_check.required_completed_activities
        )

    async def _metrics_failure_reason(
        self,
        health_check: DeploymentHealthCheck,
        subreddit_name: str,
    ) -> str | None:
        try:
            delta = await self._metrics_delta(health_check, subreddit_name)
        except ActivityError as err:
            if health_check.require_metrics:
                return f"SDK metrics unavailable: {err}"
            return None
        if delta is None:
            return None

        if delta.workflow_failed > health_check.max_sdk_workflow_failures:
            return f"SDK workflow failures increased by {delta.workflow_failed:g}"
        if delta.activity_failed > health_check.max_sdk_activity_failures:
            return f"SDK activity failures increased by {delta.activity_failed:g}"
        if delta.workflow_task_failed > health_check.max_sdk_workflow_task_failures:
            return (
                "SDK workflow task failures increased by "
                f"{delta.workflow_task_failed:g}"
            )
        return None

    async def _metrics_delta(
        self,
        health_check: DeploymentHealthCheck,
        subreddit_name: str,
    ) -> SdkMetricsSnapshot | None:
        try:
            current = await workflow.execute_activity(
                "collect_sdk_metrics",
                args=[health_check, subreddit_name],
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=DEPLOYMENT_RETRY_POLICY,
                result_type=SdkMetricsSnapshot,
                summary=health_check.build_id,
            )
        except ActivityError as err:
            if health_check.require_metrics:
                raise
            workflow.logger.info("SDK metrics unavailable; skipping check: %s", err)
            return None

        if self._baseline_metrics is None:
            self._baseline_metrics = current
            return SdkMetricsSnapshot()

        return current.delta_from(self._baseline_metrics)

    @staticmethod
    def _find_current_container(
        containers: list[DockerContainerState],
        container_name: str | None,
        build_id: str,
    ) -> DockerContainerState | None:
        if container_name is not None:
            for container in containers:
                if container.name == container_name:
                    return container
        for container in containers:
            if container.build_id == build_id:
                return container
        return None


def _temporal_query_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
