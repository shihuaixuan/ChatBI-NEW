from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
    NodeExecutionModel,
    WorkflowArtifactCleanupModel,
    WorkflowArtifactModel,
    WorkflowCheckpointModel,
    WorkflowDefinitionModel,
    WorkflowEventModel,
    WorkflowRunModel,
)


def _index_names(model) -> set[str]:
    return {index.name for index in model.__table__.indexes if isinstance(index, Index)}


def _unique_constraint_names(model) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _check_constraint_names(model) -> set[str]:
    # 命名约束便于迁移、排障和后续契约测试稳定识别。
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_workflow_models_use_independent_table_names():
    assert WorkflowDefinitionModel.__tablename__ == "workflow_definition"
    assert WorkflowRunModel.__tablename__ == "workflow_run"
    assert NodeExecutionModel.__tablename__ == "node_execution"
    assert WorkflowCheckpointModel.__tablename__ == "workflow_checkpoint"
    assert WorkflowEventModel.__tablename__ == "workflow_event"
    assert InteractionRequestModel.__tablename__ == "interaction_request"
    assert WorkflowArtifactModel.__tablename__ == "workflow_artifact"


def test_workflow_run_has_versioning_indexes_and_json_context():
    columns = WorkflowRunModel.__table__.c

    assert isinstance(columns.context.type, JSONB)
    assert isinstance(columns.request.type, JSONB)
    assert columns.version.nullable is False
    assert {
        "idx_workflow_run_status",
        "idx_workflow_run_definition",
        "idx_workflow_run_request",
    }.issubset(_index_names(WorkflowRunModel))


def test_workflow_run_declares_chat_ownership_columns_and_indexes():
    # Graph 执行归属允许为空，避免迁移时推断既有 Run 的会话关系。
    columns = WorkflowRunModel.__table__.c

    assert columns.chat_id.nullable is True
    assert columns.record_id.nullable is True
    assert {
        "idx_workflow_run_chat",
        "ux_workflow_run_record",
    }.issubset(_index_names(WorkflowRunModel))
    assert "ck_workflow_run_chat_ownership" in _check_constraint_names(WorkflowRunModel)


def test_workflow_artifact_cleanup_model_is_retryable():
    # 清理任务必须保留状态和尝试次数，供会话删除后的异步重试使用。
    assert WorkflowArtifactCleanupModel.__tablename__ == "workflow_artifact_cleanup"
    columns = WorkflowArtifactCleanupModel.__table__.c
    assert columns.artifact_id.nullable is False
    assert columns.status.nullable is False
    assert columns.attempts.nullable is False


def test_node_execution_event_and_interaction_constraints_are_declared():
    assert "ux_node_execution_attempt" in _unique_constraint_names(NodeExecutionModel)
    assert "ux_workflow_event_sequence" in _unique_constraint_names(WorkflowEventModel)
    assert {
        "idx_interaction_request_pending",
        "idx_interaction_request_run",
    }.issubset(_index_names(InteractionRequestModel))


def test_large_payload_tables_use_jsonb_and_artifact_references():
    assert isinstance(WorkflowCheckpointModel.__table__.c.context.type, JSONB)
    assert isinstance(WorkflowEventModel.__table__.c.public_payload.type, JSONB)
    assert isinstance(WorkflowEventModel.__table__.c.internal_payload.type, JSONB)
    assert isinstance(WorkflowArtifactModel.__table__.c.metadata_json.type, JSONB)
