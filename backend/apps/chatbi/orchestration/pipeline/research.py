"""RESEARCH 模式占位。真实研究循环在 P2-1 实现。"""

from typing import NoReturn


class ResearchModeNotReadyError(NotImplementedError):
    """当前版本尚未提供 RESEARCH 模式执行器。"""


class ResearchPipeline:
    def run(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise ResearchModeNotReadyError("RESEARCH_MODE_P2_1_NOT_READY")


__all__ = ["ResearchModeNotReadyError", "ResearchPipeline"]
