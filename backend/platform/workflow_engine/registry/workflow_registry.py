import hashlib
import json
from dataclasses import dataclass

from sqlbot_platform.workflow_engine.domain.definition import WorkflowDefinition
from sqlbot_platform.workflow_engine.registry.definition_validator import (
    DefinitionValidator,
)


class DuplicateDefinitionError(ValueError):
    pass


class DefinitionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class PublishedDefinition:
    """发布结果；digest 用于将 Run 固定到定义的精确内容。"""

    name: str
    version: str
    digest: str


class WorkflowRegistry:
    """内存版不可变流程定义仓库。

    Registry 保存规范化 JSON，而不是调用方传入的 Pydantic 对象。这样发布者和读取者
    后续修改自己的对象时，都无法改变已经发布的流程内容。
    """

    def __init__(self, validator: DefinitionValidator) -> None:
        self._validator = validator
        self._definitions: dict[tuple[str, str], str] = {}
        self._digests: dict[tuple[str, str], str] = {}

    def publish(self, definition: WorkflowDefinition) -> PublishedDefinition:
        key = (definition.name, definition.version)
        if key in self._definitions:
            raise DuplicateDefinitionError(
                f"DEFINITION_ALREADY_EXISTS: {definition.name}:{definition.version}"
            )

        self._validator.validate(definition)
        payload = json.dumps(
            definition.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
        self._definitions[key] = payload
        self._digests[key] = digest
        return PublishedDefinition(definition.name, definition.version, digest)

    def get(self, name: str, version: str) -> WorkflowDefinition:
        key = (name, version)
        try:
            payload = self._definitions[key]
        except KeyError:
            raise DefinitionNotFoundError(f"DEFINITION_NOT_FOUND: {name}:{version}") from None
        # 每次反序列化产生独立对象，调用方无法污染 Registry 内部状态。
        return WorkflowDefinition.model_validate_json(payload)

    def get_digest(self, name: str, version: str) -> str:
        key = (name, version)
        try:
            return self._digests[key]
        except KeyError:
            raise DefinitionNotFoundError(f"DEFINITION_NOT_FOUND: {name}:{version}") from None
