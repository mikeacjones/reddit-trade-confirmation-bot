"""Workflow that cleans up drained Docker-backed worker deployments."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import VersioningBehavior
from temporalio.exceptions import ActivityError

from temporal.deployment_models import (
    DockerCleanupResult,
    DockerContainerState,
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

    @workflow.signal
    def deployed(self, build_id: str) -> None:
        """Signal that a new build was deployed for this subreddit."""
        if build_id not in self._seen_build_ids:
            self._seen_build_ids.append(build_id)
        self._current_build_id = build_id

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
