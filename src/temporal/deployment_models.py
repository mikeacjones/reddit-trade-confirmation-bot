"""Serializable models for worker deployment cleanup."""

from dataclasses import dataclass, field


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
