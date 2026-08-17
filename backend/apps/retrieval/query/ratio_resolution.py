"""复合比率的确定性方向校验。

该模块不根据指标名称猜测分子、分母。方向只能来自语义资产契约提供的
复合指标顺序，或来自运行时快照已经确认的 operand 角色。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RatioOperandRole = Literal["numerator", "denominator", "unknown"]


class RatioDirectionError(ValueError):
    """比率方向无法由语义资产契约证明。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(f"{code}:{message}" if message else code)


@dataclass(frozen=True, slots=True)
class RatioOperandCandidate:
    """比率操作数的绑定结果；role 必须来自语义资产运行时快照。"""

    asset_id: int
    model_id: int
    text: str
    role: RatioOperandRole = "unknown"

    def __post_init__(self) -> None:
        if self.asset_id <= 0 or self.model_id <= 0:
            raise ValueError("RATIO_OPERAND_ASSET_ID_INVALID")
        if not self.text.strip():
            raise ValueError("RATIO_OPERAND_TEXT_REQUIRED")


@dataclass(frozen=True, slots=True)
class RatioDirectionResult:
    """通过方向校验后的稳定结果。"""

    numerator: RatioOperandCandidate
    denominator: RatioOperandCandidate
    evidence: Literal["asset_definition", "operand_roles"]


def validate_ratio_direction(
    numerator: RatioOperandCandidate,
    denominator: RatioOperandCandidate,
    *,
    declared_order: tuple[int, int] | None = None,
) -> RatioDirectionResult:
    """验证比率分子、分母方向并返回可编译的结果。

    ``declared_order`` 是复合指标资产定义中的有序 metric_refs。没有资产定义时，
    只能使用运行时快照提供的 operand role；两者都不能证明方向时固定拒绝。
    """

    if numerator.model_id != denominator.model_id:
        raise RatioDirectionError(
            "RATIO_CROSS_MODEL_UNSUPPORTED",
            "比率分子和分母属于不同语义模型",
        )
    if numerator.asset_id == denominator.asset_id:
        raise RatioDirectionError(
            "RATIO_OPERANDS_IDENTICAL",
            "比率分子和分母不能引用同一指标资产",
        )

    if declared_order is not None:
        if declared_order != (numerator.asset_id, denominator.asset_id):
            raise RatioDirectionError(
                "RATIO_DIRECTION_MISMATCH",
                "绑定的分子分母顺序与语义资产定义不一致",
            )
        return RatioDirectionResult(
            numerator=numerator,
            denominator=denominator,
            evidence="asset_definition",
        )

    roles = (numerator.role, denominator.role)
    if roles == ("numerator", "denominator"):
        return RatioDirectionResult(
            numerator=numerator,
            denominator=denominator,
            evidence="operand_roles",
        )
    if roles == ("denominator", "numerator"):
        raise RatioDirectionError(
            "RATIO_DIRECTION_MISMATCH",
            "模型绑定结果把分子和分母顺序反置",
        )
    raise RatioDirectionError(
        "RATIO_DIRECTION_UNPROVEN",
        "语义资产没有提供可证明的分子分母方向",
    )


__all__ = [
    "RatioDirectionError",
    "RatioDirectionResult",
    "RatioOperandCandidate",
    "RatioOperandRole",
    "validate_ratio_direction",
]
