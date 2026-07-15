import json
from pathlib import Path

import pytest

from apps.workflow_engine.infrastructure.artifacts.file_store import FileArtifactStore


class FakeArtifactMetadataStore:
    def __init__(self, fail_on_put: bool = False) -> None:
        self.fail_on_put = fail_on_put
        self.artifacts = {}

    def put(self, artifact):
        if self.fail_on_put:
            raise RuntimeError("metadata unavailable")
        self.artifacts[artifact.artifact_id] = artifact
        return artifact

    def get(self, artifact_id: str):
        return self.artifacts[artifact_id]


def test_file_artifact_store_persists_json_body_and_metadata(tmp_path):
    metadata_store = FakeArtifactMetadataStore()
    store = FileArtifactStore(root=tmp_path, metadata_store=metadata_store)

    ref = store.put_json(
        run_id="run-1",
        kind="sql_result",
        payload={"query_id": "query-0", "rows": [{"value": 1}], "row_count": 1},
        metadata={"query_id": "query-0", "row_count": 1},
    )

    artifact, content = store.get(ref.artifact_id)
    assert json.loads(content) == {
        "query_id": "query-0",
        "row_count": 1,
        "rows": [{"value": 1}],
    }
    assert artifact.digest == ref.digest
    assert artifact.size == len(content)
    assert artifact.storage_uri.startswith("file://")
    assert artifact.metadata == {"query_id": "query-0", "row_count": 1}


def test_file_artifact_store_removes_body_when_metadata_write_fails(tmp_path):
    store = FileArtifactStore(
        root=tmp_path,
        metadata_store=FakeArtifactMetadataStore(fail_on_put=True),
    )

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        store.put_json(
            run_id="run-1",
            kind="sql_result",
            payload={"rows": [{"value": 1}]},
        )

    assert list(tmp_path.iterdir()) == []


def test_file_artifact_store_rejects_metadata_uri_outside_root(tmp_path):
    metadata_store = FakeArtifactMetadataStore()
    store = FileArtifactStore(root=tmp_path, metadata_store=metadata_store)
    ref = store.put_json(run_id="run-1", kind="sql_result", payload={"rows": []})
    artifact = metadata_store.artifacts[ref.artifact_id]
    artifact.storage_uri = (Path(tmp_path).parent / "secret.json").resolve().as_uri()

    with pytest.raises(ValueError, match="ARTIFACT_PATH_OUTSIDE_ROOT"):
        store.get(ref.artifact_id)
