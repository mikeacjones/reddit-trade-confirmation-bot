"""Activities for Temporal Worker Deployment and Docker cleanup."""

import http.client
import json
import os
import re
import socket
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from temporalio import activity
from temporalio.api.deployment.v1 import WorkerDeploymentInfo
from temporalio.api.enums.v1 import (
    VersionDrainageStatus,
    WorkerDeploymentVersionStatus,
)
from temporalio.api.workflowservice.v1 import (
    DescribeWorkerDeploymentRequest,
    ListWorkflowExecutionsRequest,
    SetWorkerDeploymentCurrentVersionRequest,
)
from temporalio.client import Client

from bot.config import TASK_QUEUE, TEMPORAL_HOST, TEMPORAL_NAMESPACE
from temporal.deployment_models import (
    DeploymentHealthCheck,
    DeploymentRollbackResult,
    DockerCleanupResult,
    DockerContainerState,
    SdkMetricsSnapshot,
    TemporalExecutionSummary,
    WorkerDeploymentState,
    WorkerDeploymentVersionState,
)

DEPLOYMENT_LABEL = "com.reddit-bots.deployment-name"
BUILD_ID_LABEL = "com.reddit-bots.build-id"
IMAGE_LABEL = "com.reddit-bots.image"
DEFAULT_DOCKER_SOCKET = "/var/run/docker.sock"
DEPLOYMENT_ACTIVITY_TYPES = {
    "collect_sdk_metrics",
    "count_completed_workflows_for_deployment",
    "count_failed_workflows_for_deployment",
    "describe_worker_deployment",
    "list_deployment_containers",
    "remove_deployment_container",
    "rollback_worker_deployment",
}
PROMETHEUS_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+"
    r"(?P<value>[-+0-9.eE]+)"
)


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    """Minimal HTTP client for the Docker Engine Unix socket API."""

    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        self.sock = sock


def _docker_socket_path() -> str:
    return os.getenv("DOCKER_SOCKET", DEFAULT_DOCKER_SOCKET)


def _docker_request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    ok_statuses: set[int] | None = None,
) -> Any:
    socket_path = _docker_socket_path()
    if not os.path.exists(socket_path):
        raise RuntimeError(f"Docker socket not found: {socket_path}")

    ok_statuses = ok_statuses or {200, 201, 204}
    encoded_body = None
    headers = {}
    if body is not None:
        encoded_body = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    conn = UnixSocketHTTPConnection(socket_path)
    try:
        conn.request(method, path, body=encoded_body, headers=headers)
        response = conn.getresponse()
        response_body = response.read()
    finally:
        conn.close()

    if response.status not in ok_statuses:
        detail = response_body.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Docker API {method} {path} failed with {response.status}: {detail}"
        )

    if not response_body:
        return None
    return json.loads(response_body.decode("utf-8"))


def _enum_suffix(enum_wrapper: Any, value: int, prefix: str) -> str:
    name = enum_wrapper.Name(value)
    if name.startswith(prefix):
        return name.removeprefix(prefix)
    return name


def _visibility_query_string(value: str) -> str:
    """Escape a string for use in a Temporal visibility query."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _version_summary_to_state(
    summary: WorkerDeploymentInfo.WorkerDeploymentVersionSummary,
) -> WorkerDeploymentVersionState:
    build_id = summary.deployment_version.build_id
    if not build_id:
        build_id = summary.version.rsplit(".", 1)[-1]

    drainage_status = None
    if summary.drainage_status:
        drainage_status = _enum_suffix(
            VersionDrainageStatus,
            summary.drainage_status,
            "VERSION_DRAINAGE_STATUS_",
        )
    elif summary.HasField("drainage_info") and summary.drainage_info.status:
        drainage_status = _enum_suffix(
            VersionDrainageStatus,
            summary.drainage_info.status,
            "VERSION_DRAINAGE_STATUS_",
        )

    return WorkerDeploymentVersionState(
        build_id=build_id,
        status=_enum_suffix(
            WorkerDeploymentVersionStatus,
            summary.status,
            "WORKER_DEPLOYMENT_VERSION_STATUS_",
        ),
        drainage_status=drainage_status,
    )


def _labels_from_prometheus_sample(raw_labels: str | None) -> dict[str, str]:
    if not raw_labels:
        return {}

    labels: dict[str, str] = {}
    label_pattern = r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"'
    for item in re.finditer(label_pattern, raw_labels):
        labels[item.group(1)] = item.group(2).replace(r"\"", '"')
    return labels


def _sample_matches(
    labels: dict[str, str],
    *,
    namespace: str,
    task_queue: str,
    subreddit_name: str,
    exclude_activity_types: set[str] | None = None,
) -> bool:
    if (
        exclude_activity_types
        and labels.get("activity_type") in exclude_activity_types
    ):
        return False

    expected = {
        "namespace": namespace,
        "task_queue": task_queue,
        "subreddit": subreddit_name,
    }
    for key, value in expected.items():
        if key in labels and labels[key] != value:
            return False
    return True


def _sum_prometheus_counter(
    text: str,
    metric_names: set[str],
    *,
    namespace: str,
    task_queue: str,
    subreddit_name: str,
    exclude_activity_types: set[str] | None = None,
) -> float:
    total = 0.0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        match = PROMETHEUS_SAMPLE_RE.match(line)
        if match is None:
            continue

        name = match.group("name")
        if name not in metric_names and not (
            name.endswith("_total") and name.removesuffix("_total") in metric_names
        ):
            continue

        labels = _labels_from_prometheus_sample(match.group("labels"))
        if not _sample_matches(
            labels,
            namespace=namespace,
            task_queue=task_queue,
            subreddit_name=subreddit_name,
            exclude_activity_types=exclude_activity_types,
        ):
            continue

        total += float(match.group("value"))
    return total


@activity.defn
async def describe_worker_deployment(deployment_name: str) -> WorkerDeploymentState:
    """Describe a Temporal Worker Deployment and its versions."""
    client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)
    response = await client.workflow_service.describe_worker_deployment(
        DescribeWorkerDeploymentRequest(
            namespace=TEMPORAL_NAMESPACE,
            deployment_name=deployment_name,
        ),
        retry=True,
    )

    info = response.worker_deployment_info
    return WorkerDeploymentState(
        deployment_name=deployment_name,
        versions=[
            _version_summary_to_state(summary)
            for summary in info.version_summaries
        ],
    )


async def _count_workflows_for_deployment(
    deployment_name: str,
    build_id: str,
    since_time_iso: str,
    status: str,
) -> TemporalExecutionSummary:
    """Count recent workflows attributed to a deployment build by status."""
    client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)
    deployment_version = f"{deployment_name}:{build_id}"
    query = (
        f'ExecutionStatus = "{status}" '
        f'AND CloseTime >= "{since_time_iso}" '
        f'AND TemporalWorkerDeployment = "{_visibility_query_string(deployment_name)}" '
        f'AND TemporalWorkerDeploymentVersion = '
        f'"{_visibility_query_string(deployment_version)}"'
    )
    next_page_token = b""
    workflow_ids: list[str] = []

    while True:
        response = await client.workflow_service.list_workflow_executions(
            ListWorkflowExecutionsRequest(
                namespace=TEMPORAL_NAMESPACE,
                page_size=100,
                next_page_token=next_page_token,
                query=query,
            ),
            retry=True,
        )

        workflow_ids.extend(
            execution_info.execution.workflow_id
            for execution_info in response.executions
        )

        next_page_token = response.next_page_token
        if not next_page_token:
            break

    return TemporalExecutionSummary(
        count=len(workflow_ids),
        workflow_ids=workflow_ids[:20],
    )


@activity.defn
async def count_failed_workflows_for_deployment(
    deployment_name: str,
    build_id: str,
    since_time_iso: str,
) -> TemporalExecutionSummary:
    """Count recently failed workflows attributed to a deployment build."""
    return await _count_workflows_for_deployment(
        deployment_name,
        build_id,
        since_time_iso,
        "Failed",
    )


@activity.defn
async def count_completed_workflows_for_deployment(
    deployment_name: str,
    build_id: str,
    since_time_iso: str,
) -> TemporalExecutionSummary:
    """Count recently completed workflows attributed to a deployment build."""
    return await _count_workflows_for_deployment(
        deployment_name,
        build_id,
        since_time_iso,
        "Completed",
    )


@activity.defn
async def collect_sdk_metrics(
    health_check: DeploymentHealthCheck,
    subreddit_name: str,
) -> SdkMetricsSnapshot:
    """Collect Temporal SDK failure counters from the worker metrics endpoint."""
    metrics_url = health_check.metrics_url or os.getenv("DEPLOYMENT_HEALTH_METRICS_URL")
    if not metrics_url:
        bind_address = os.getenv("TEMPORAL_SDK_METRICS_BIND_ADDRESS", "").strip()
        if bind_address:
            port = bind_address.rsplit(":", 1)[-1]
            metrics_url = f"http://127.0.0.1:{port}/metrics"

    if not metrics_url:
        raise RuntimeError("No SDK metrics URL configured")

    with urlopen(metrics_url, timeout=5) as response:
        metrics_text = response.read().decode("utf-8", errors="replace")

    common = {
        "namespace": TEMPORAL_NAMESPACE,
        "task_queue": TASK_QUEUE,
        "subreddit_name": subreddit_name,
    }
    return SdkMetricsSnapshot(
        workflow_completed=_sum_prometheus_counter(
            metrics_text,
            {"temporal_workflow_completed"},
            **common,
        ),
        activity_completed=_sum_prometheus_counter(
            metrics_text,
            {
                "temporal_activity_execution_completed",
                "temporal_activity_execution_latency_count",
            },
            exclude_activity_types=DEPLOYMENT_ACTIVITY_TYPES,
            **common,
        ),
        workflow_failed=_sum_prometheus_counter(
            metrics_text,
            {"temporal_workflow_failed"},
            **common,
        ),
        activity_failed=_sum_prometheus_counter(
            metrics_text,
            {"temporal_activity_execution_failed"},
            **common,
        ),
        workflow_task_failed=_sum_prometheus_counter(
            metrics_text,
            {
                "temporal_workflow_task_execution_failed",
                "temporal_workflow_task_failed",
            },
            **common,
        ),
    )


@activity.defn
async def list_deployment_containers(
    deployment_name: str,
) -> list[DockerContainerState]:
    """List Docker containers associated with a Temporal deployment."""
    filters = json.dumps({"label": [f"{DEPLOYMENT_LABEL}={deployment_name}"]})
    query = urlencode({"all": "true", "filters": filters})
    containers = _docker_request("GET", f"/containers/json?{query}") or []

    result: list[DockerContainerState] = []
    for container in containers:
        labels = container.get("Labels") or {}
        build_id = labels.get(BUILD_ID_LABEL)
        if not build_id:
            continue

        names = container.get("Names") or []
        name = names[0].lstrip("/") if names else container["Id"][:12]
        result.append(
            DockerContainerState(
                id=container["Id"],
                name=name,
                build_id=build_id,
                image=labels.get(IMAGE_LABEL) or container.get("Image"),
                state=container.get("State"),
                status=container.get("Status"),
            )
        )

    return result


@activity.defn
async def rollback_worker_deployment(
    deployment_name: str,
    rollback_build_id: str,
    failed_container: DockerContainerState | None = None,
) -> DeploymentRollbackResult:
    """Set a Worker Deployment back to a previous build and remove failed container."""
    client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)
    await client.workflow_service.set_worker_deployment_current_version(
        SetWorkerDeploymentCurrentVersionRequest(
            namespace=TEMPORAL_NAMESPACE,
            deployment_name=deployment_name,
            build_id=rollback_build_id,
            identity="reddit-bots-deployment-health",
        ),
        retry=True,
    )

    result = DeploymentRollbackResult(
        deployment_name=deployment_name,
        rollback_build_id=rollback_build_id,
    )
    if failed_container is not None:
        container_ref = quote(failed_container.id, safe="")
        _docker_request(
            "DELETE",
            f"/containers/{container_ref}?force=true&v=true",
            ok_statuses={204, 404},
        )
        result.container_removed = True
    return result


@activity.defn
async def remove_deployment_container(
    container: DockerContainerState,
) -> DockerCleanupResult:
    """Remove a drained deployment container and its now-unused image."""
    result = DockerCleanupResult(container_name=container.name)
    container_ref = quote(container.id, safe="")

    _docker_request(
        "DELETE",
        f"/containers/{container_ref}?force=true&v=true",
        ok_statuses={204, 404},
    )
    result.container_removed = True

    prune_filters = json.dumps({"label": [f"{BUILD_ID_LABEL}={container.build_id}"]})
    prune_query = urlencode({"filters": prune_filters})
    prune_response = _docker_request("POST", f"/containers/prune?{prune_query}") or {}
    deleted = prune_response.get("ContainersDeleted") or []
    result.pruned_containers = len(deleted)

    if container.image:
        image_ref = quote(container.image, safe="")
        try:
            _docker_request(
                "DELETE",
                f"/images/{image_ref}?force=false&noprune=false",
                ok_statuses={200, 202, 204, 404},
            )
            result.image_removed = True
        except RuntimeError as err:
            # A 409 means another container still uses the image. Report it but
            # do not fail the workflow; a future deployment cleanup can retry.
            result.image_remove_error = str(err)

    return result
