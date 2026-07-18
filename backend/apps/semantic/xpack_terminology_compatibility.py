"""为当前 XPack 审计版本提供 Semantic 术语查询映射。"""

from typing import Any

from sqlalchemy.orm import aliased

from apps.semantic.models.orm import SemanticTerm

# XPack 仍按旧名称读取 ``id`` 和 ``word``；别名必须指向 Semantic 表。
XpackTerminology: Any = aliased(
    SemanticTerm,
    name="semantic_terminology_compat",
)
XpackTerminology.word = XpackTerminology.name
