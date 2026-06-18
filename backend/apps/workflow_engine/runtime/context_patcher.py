
from pydantic import ValidationError

from apps.workflow_engine.domain.context import ContextPatch, WorkflowContext
from apps.workflow_engine.runtime.path import (
    PathResolutionError,
    read_path,
    remove_path,
    split_path,
    write_path,
)


class ContextPatchError(ValueError):
    """Patch 拒绝时返回的稳定错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ContextPatcher:
    """以全有或全无方式合并节点状态补丁。"""

    _NODE_WRITABLE_NAMESPACES = frozenset({"conversation", "variables"})

    def apply(self, context: WorkflowContext, patch: ContextPatch) -> WorkflowContext:
        """在副本上应用全部操作，最终重新构造 Context。

        不直接修改 Pydantic 对象有两个原因：一是保证后续操作失败时可以完整回滚；
        二是让最终结果再次经过字段类型校验，避免非法结构进入 Checkpoint。
        """

        data = context.model_dump(mode="python")
        self._validate_paths(patch)

        try:
            for path, value in patch.set_values.items():
                write_path(data, path, value)

            for path, values in patch.append_values.items():
                try:
                    target = read_path(data, path)
                except PathResolutionError:
                    write_path(data, path, [])
                    target = read_path(data, path)
                if not isinstance(target, list):
                    raise ContextPatchError(
                        "PATCH_TARGET_NOT_LIST",
                        f"追加目标 {path!r} 不是列表",
                    )
                target.extend(values)

            for path in patch.remove_paths:
                remove_path(data, path)

            for name, artifact in patch.artifact_updates.items():
                data["artifacts"][name] = artifact.model_dump(mode="python")
        except ContextPatchError:
            raise
        except PathResolutionError as exc:
            raise ContextPatchError("INVALID_CONTEXT_PATH", str(exc)) from exc

        try:
            return WorkflowContext.model_validate(data)
        except ValidationError as exc:
            raise ContextPatchError("INVALID_CONTEXT_PATCH", str(exc)) from exc

    def _validate_paths(self, patch: ContextPatch) -> None:
        all_paths: list[str] = [
            *patch.set_values,
            *patch.append_values,
            *patch.remove_paths,
        ]
        for path in all_paths:
            try:
                root = split_path(path)[0]
            except PathResolutionError as exc:
                raise ContextPatchError("INVALID_CONTEXT_PATH", str(exc)) from exc
            if root not in self._NODE_WRITABLE_NAMESPACES:
                raise ContextPatchError(
                    "PATCH_PATH_FORBIDDEN",
                    f"节点不能修改命名空间 {root!r}",
                )
