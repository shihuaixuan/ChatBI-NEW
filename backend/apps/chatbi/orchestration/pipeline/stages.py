"""固定阶段服务封装。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class PipelineStageError(RuntimeError):
    """阶段没有配置实现或实现返回了非法结果。"""


@dataclass(frozen=True, slots=True)
class StageResult(Generic[T]):
    value: T
    metadata: dict[str, Any]


StageCallable = Callable[..., Any]


class PipelineStages:
    """把 bind/plan/validate/execute/compute/answer 统一成可注入阶段。"""

    def __init__(
        self,
        *,
        bind: StageCallable | None = None,
        plan: StageCallable | None = None,
        validate: StageCallable | None = None,
        execute: StageCallable | None = None,
        compute: StageCallable | None = None,
        answer: StageCallable | None = None,
    ) -> None:
        self._handlers = {
            "bind": bind,
            "plan": plan,
            "validate": validate,
            "execute": execute,
            "compute": compute,
            "answer": answer,
        }

    def bind(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("bind", *args, **kwargs)

    def plan(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("plan", *args, **kwargs)

    def validate(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("validate", *args, **kwargs)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("execute", *args, **kwargs)

    def compute(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("compute", *args, **kwargs)

    def answer(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("answer", *args, **kwargs)

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        handler = self._handlers[name]
        if handler is None:
            raise PipelineStageError(f"PIPELINE_STAGE_NOT_CONFIGURED:{name}")
        return handler(*args, **kwargs)


__all__ = ["PipelineStageError", "PipelineStages", "StageResult"]
