from threading import RLock

from apps.workflow_engine.domain.artifact import WorkflowArtifact
from apps.workflow_engine.domain.event import WorkflowEvent
from apps.workflow_engine.domain.run import WorkflowRun


class RunVersionConflictError(RuntimeError):
    pass


class InMemoryRunStore:
    """P0 使用的线程安全 Run Store，也是数据库实现的契约基准。"""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._lock = RLock()

    def create(self, run: WorkflowRun) -> WorkflowRun:
        with self._lock:
            if run.run_id in self._runs:
                raise ValueError(f"RUN_ALREADY_EXISTS: {run.run_id}")
            stored = run.model_copy(deep=True)
            self._runs[run.run_id] = stored
            return stored.model_copy(deep=True)

    def get(self, run_id: str) -> WorkflowRun:
        with self._lock:
            try:
                return self._runs[run_id].model_copy(deep=True)
            except KeyError:
                raise KeyError(f"RUN_NOT_FOUND: {run_id}") from None

    def save(self, run: WorkflowRun, expected_version: int) -> WorkflowRun:
        with self._lock:
            current = self._runs.get(run.run_id)
            if current is None:
                raise KeyError(f"RUN_NOT_FOUND: {run.run_id}")
            if current.version != expected_version:
                raise RunVersionConflictError(
                    f"RUN_VERSION_CONFLICT: expected={expected_version}, actual={current.version}"
                )
            stored = run.model_copy(update={"version": expected_version + 1}, deep=True)
            self._runs[run.run_id] = stored
            return stored.model_copy(deep=True)


class InMemoryArtifactStore:
    """同时保存元数据与字节正文的内存 Artifact Store。"""

    def __init__(self) -> None:
        self._artifacts: dict[str, tuple[WorkflowArtifact, bytes]] = {}

    def put(self, artifact: WorkflowArtifact, content: bytes) -> WorkflowArtifact:
        stored = artifact.model_copy(deep=True)
        self._artifacts[artifact.artifact_id] = (stored, bytes(content))
        return stored.model_copy(deep=True)

    def get(self, artifact_id: str) -> tuple[WorkflowArtifact, bytes]:
        try:
            artifact, content = self._artifacts[artifact_id]
        except KeyError:
            raise KeyError(f"ARTIFACT_NOT_FOUND: {artifact_id}") from None
        return artifact.model_copy(deep=True), bytes(content)


class InMemoryEventPublisher:
    """按 run_id 和 sequence 有序保存事件。"""

    def __init__(self) -> None:
        self._events: dict[str, dict[int, WorkflowEvent]] = {}

    def publish(self, event: WorkflowEvent) -> None:
        events = self._events.setdefault(event.run_id, {})
        if event.sequence in events:
            raise ValueError(f"EVENT_SEQUENCE_CONFLICT: {event.run_id}:{event.sequence}")
        events[event.sequence] = event.model_copy(deep=True)

    def list(self, run_id: str, after_sequence: int = 0) -> list[WorkflowEvent]:
        events = self._events.get(run_id, {})
        return [
            events[sequence].model_copy(deep=True)
            for sequence in sorted(events)
            if sequence > after_sequence
        ]
