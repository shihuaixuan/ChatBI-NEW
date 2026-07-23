from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlmodel import Session, select

from sqlbot_platform.workflow_engine.domain.interaction import (
    InteractionRequest,
    InteractionStatus,
)
from sqlbot_platform.workflow_engine.infrastructure.persistence.models import (
    InteractionRequestModel,
)
from sqlbot_platform.workflow_engine.runtime.interaction import InteractionError


class DatabaseInteractionManager:
    """数据库版交互请求管理器，供 API Runtime 创建和恢复等待输入的 Run。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, run_id: str, node_name: str, spec: dict[str, Any]) -> InteractionRequest:
        interaction = InteractionRequestModel(
            interaction_id=str(uuid4()),
            run_id=run_id,
            node_name=node_name,
            status=InteractionStatus.PENDING.value,
            response_schema=spec.get("response_schema", {}),
            allowed_update_paths=spec.get("allowed_update_paths", []),
            prompt=spec.get("prompt"),
            options=spec.get("options", []),
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(interaction)
        self._session.flush()
        return self._to_domain(interaction)

    def get(self, interaction_id: str | None) -> InteractionRequest:
        if interaction_id is None:
            raise InteractionError("INTERACTION_NOT_FOUND", "Run 没有待处理交互")
        model = self._session.exec(
            select(InteractionRequestModel).where(InteractionRequestModel.interaction_id == interaction_id)
        ).one_or_none()
        if model is None:
            raise InteractionError("INTERACTION_NOT_FOUND", interaction_id)
        return self._to_domain(model)

    def answer(self, interaction_id: str, response: dict[str, Any]) -> InteractionRequest:
        model = self._session.exec(
            select(InteractionRequestModel).where(InteractionRequestModel.interaction_id == interaction_id)
        ).one_or_none()
        if model is None:
            raise InteractionError("INTERACTION_NOT_FOUND", interaction_id)
        interaction = self._to_domain(model)
        if interaction.status is not InteractionStatus.PENDING:
            raise InteractionError("INTERACTION_ALREADY_ANSWERED", interaction_id)
        self._validate_response(interaction.response_schema, response)
        model.status = InteractionStatus.ANSWERED.value
        model.response = response
        model.answered_at = datetime.now(timezone.utc)
        self._session.add(model)
        self._session.flush()
        return self._to_domain(model)

    def _to_domain(self, model: InteractionRequestModel) -> InteractionRequest:
        return InteractionRequest(
            interaction_id=model.interaction_id,
            run_id=model.run_id,
            node_name=model.node_name,
            status=InteractionStatus(model.status),
            response_schema=model.response_schema or {},
            allowed_update_paths=model.allowed_update_paths or [],
            prompt=model.prompt,
            options=model.options or [],
            response=model.response,
            created_at=model.created_at or datetime.now(timezone.utc),
            answered_at=model.answered_at,
            expires_at=model.expires_at,
        )

    def _validate_response(self, schema: dict[str, Any], response: dict[str, Any]) -> None:
        """校验当前交互使用的 JSON Schema 子集，与内存管理器保持一致。"""

        if schema.get("type") == "object" and not isinstance(response, dict):
            raise InteractionError("INTERACTION_RESPONSE_INVALID", "回答必须是对象")
        for field in schema.get("required", []):
            if field not in response:
                raise InteractionError("INTERACTION_RESPONSE_INVALID", f"缺少必填字段 {field!r}")
        properties = schema.get("properties", {})
        python_types = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
        for field, value in response.items():
            expected_name = properties.get(field, {}).get("type")
            expected_type = python_types.get(expected_name)
            if expected_type is not None and not isinstance(value, expected_type):
                raise InteractionError(
                    "INTERACTION_RESPONSE_INVALID",
                    f"字段 {field!r} 类型不符合 {expected_name!r}",
                )
