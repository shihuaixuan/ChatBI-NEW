from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from apps.workflow_engine.domain.interaction import (
    InteractionRequest,
    InteractionStatus,
)


class InteractionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class InteractionManager:
    """P0 内存版交互请求仓库与响应校验器。"""

    def __init__(self) -> None:
        self._interactions: dict[str, InteractionRequest] = {}

    def create(self, run_id: str, node_name: str, spec: dict[str, Any]) -> InteractionRequest:
        interaction = InteractionRequest(
            interaction_id=str(uuid4()),
            run_id=run_id,
            node_name=node_name,
            response_schema=spec.get("response_schema", {}),
            allowed_update_paths=spec.get("allowed_update_paths", []),
            prompt=spec.get("prompt"),
            options=spec.get("options", []),
            created_at=datetime.now(timezone.utc),
        )
        self._interactions[interaction.interaction_id] = interaction
        return interaction.model_copy(deep=True)

    def get(self, interaction_id: str | None) -> InteractionRequest:
        if interaction_id is None:
            raise InteractionError("INTERACTION_NOT_FOUND", "Run 没有待处理交互")
        try:
            return self._interactions[interaction_id].model_copy(deep=True)
        except KeyError:
            raise InteractionError("INTERACTION_NOT_FOUND", interaction_id) from None

    def answer(self, interaction_id: str, response: dict[str, Any]) -> InteractionRequest:
        interaction = self.get(interaction_id)
        if interaction.status is not InteractionStatus.PENDING:
            raise InteractionError("INTERACTION_ALREADY_ANSWERED", interaction_id)
        self._validate_response(interaction.response_schema, response)
        interaction.status = InteractionStatus.ANSWERED
        interaction.response = response
        interaction.answered_at = datetime.now(timezone.utc)
        self._interactions[interaction_id] = interaction
        return interaction.model_copy(deep=True)

    def _validate_response(self, schema: dict[str, Any], response: dict[str, Any]) -> None:
        """校验当前交互使用的 JSON Schema 子集。

        首期仅支持 object、required 与基础 property type，足以覆盖结构化澄清；
        P1 API 层可替换为完整 JSON Schema 校验器而不改变领域协议。
        """

        if schema.get("type") == "object" and not isinstance(response, dict):
            raise InteractionError("INTERACTION_RESPONSE_INVALID", "回答必须是对象")
        for field in schema.get("required", []):
            if field not in response:
                raise InteractionError(
                    "INTERACTION_RESPONSE_INVALID",
                    f"缺少必填字段 {field!r}",
                )
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
