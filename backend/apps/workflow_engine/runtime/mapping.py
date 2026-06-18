from typing import Any

from apps.workflow_engine.domain.context import ContextPatch, WorkflowContext
from apps.workflow_engine.runtime.path import PathResolutionError, read_path, split_path


class MappingError(ValueError):
    """节点输入输出映射失败时返回的稳定错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class MappingResolver:
    """在 WorkflowContext 与节点局部数据之间执行显式映射。"""

    _OUTPUT_NAMESPACES = frozenset({"conversation", "variables"})

    def resolve_inputs(
        self,
        context: WorkflowContext,
        mapping: dict[str, str],
    ) -> dict[str, Any]:
        source = context.model_dump(mode="python")
        inputs: dict[str, Any] = {}
        for input_name, context_path in mapping.items():
            try:
                inputs[input_name] = read_path(source, context_path)
            except PathResolutionError as exc:
                raise MappingError(
                    "MAPPING_PATH_NOT_FOUND",
                    f"节点输入 {input_name!r} 对应路径 {context_path!r} 不存在",
                ) from exc
        return inputs

    def resolve_outputs(
        self,
        outputs: dict[str, Any],
        mapping: dict[str, str],
    ) -> ContextPatch:
        set_values: dict[str, Any] = {}
        for output_name, context_path in mapping.items():
            if output_name not in outputs:
                raise MappingError(
                    "MAPPING_OUTPUT_NOT_FOUND",
                    f"节点输出 {output_name!r} 不存在",
                )
            try:
                root = split_path(context_path)[0]
            except PathResolutionError as exc:
                raise MappingError("INVALID_MAPPING_TARGET", str(exc)) from exc
            if root not in self._OUTPUT_NAMESPACES:
                raise MappingError(
                    "MAPPING_TARGET_FORBIDDEN",
                    f"节点输出不能写入命名空间 {root!r}",
                )
            set_values[context_path] = outputs[output_name]
        return ContextPatch(set_values=set_values)
