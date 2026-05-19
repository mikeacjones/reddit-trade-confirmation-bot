"""Activities for Temporal Worker Deployment and Docker cleanup."""

import http.client
import json
import os
import socket
from typing import Any
from urllib.parse import quote, urlencode

from temporalio import activity
from temporalio.api.deployment.v1 import WorkerDeploymentInfo
from temporalio.api.enums.v1 import (
    VersionDrainageStatus,
    WorkerDeploymentVersionStatus,
)
from temporalio.api.workflowservice.v1 import DescribeWorkerDeploymentRequest
from temporalio.client import Client

from bot.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE
from temporal.deployment_models import (
    DockerCleanupResult,
    DockerContainerState,
    WorkerDeploymentState,
    WorkerDeploymentVersionState,
)

DEPLOYMENT_LABEL = "com.reddit-bots.deployment-name"
BUILD_ID_LABEL = "com.reddit-bots.build-id"
IMAGE_LABEL = "com.reddit-bots.image"
DEFAULT_DOCKER_SOCKET = "/var/run/docker.sock"


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
