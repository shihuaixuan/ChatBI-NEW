from __future__ import annotations

from typing import Protocol


class RelationModel(Protocol):
    """参与模型关系校验所需的最小模型状态。"""

    oid: int
    domain_id: int
    status: int


class ModelRelationRuleError(ValueError):
    """模型关系规则错误基类。"""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class RelationModelUnavailableError(ModelRelationRuleError):
    """参与关系的模型不存在或不可用。"""


class RelationDomainMismatchError(ModelRelationRuleError):
    """参与关系的模型不属于指定主题域。"""


def validate_model_relation(
    *,
    oid: int,
    domain_id: int,
    left_model: RelationModel | None,
    right_model: RelationModel | None,
) -> None:
    """校验模型关系两端的可见性和主题域一致性。"""
    if left_model is None or right_model is None:
        raise RelationModelUnavailableError("SEMANTIC_MODEL_NOT_FOUND")
    if left_model.oid != oid or right_model.oid != oid:
        raise RelationModelUnavailableError("SEMANTIC_MODEL_NOT_FOUND")
    if left_model.status != 1 or right_model.status != 1:
        raise RelationModelUnavailableError("SEMANTIC_MODEL_NOT_FOUND")
    if left_model.domain_id != domain_id or right_model.domain_id != domain_id:
        raise RelationDomainMismatchError(
            "SEMANTIC_MODEL_RELATION_DOMAIN_MISMATCH"
        )
