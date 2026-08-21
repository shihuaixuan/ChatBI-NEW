"""跨模型关系路径的统一校验规则。"""

from collections.abc import Collection, Mapping

from apps.semantic.models.orm import SemanticModelRelation


def relation_path_error(
    relation_path: tuple[int, ...],
    source_model_id: int,
    target_model_id: int,
    relations: Mapping[int, SemanticModelRelation],
    *,
    allowed_contract_statuses: Collection[str],
) -> str | None:
    """返回关系路径的首个错误；返回 ``None`` 表示路径可执行。"""

    if not relation_path:
        return "EMPTY"
    current_model_id = source_model_id
    for relation_id in relation_path:
        relation = relations.get(relation_id)
        if relation is None:
            return "REFERENCE"
        if relation.contract_status not in allowed_contract_statuses:
            return "STATUS"
        if relation.aggregation_safety == "FORBIDDEN":
            return "AGGREGATION_SAFETY"
        if relation.left_model_id == current_model_id:
            if relation.metric_propagation not in {"LEFT_TO_RIGHT", "BOTH"}:
                return "PROPAGATION"
            current_model_id = relation.right_model_id
        elif relation.right_model_id == current_model_id:
            if relation.metric_propagation not in {"RIGHT_TO_LEFT", "BOTH"}:
                return "PROPAGATION"
            current_model_id = relation.left_model_id
        else:
            return "CONTINUITY"
    return None if current_model_id == target_model_id else "TARGET"

