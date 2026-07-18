"""授权请求与调用者 DTO。"""

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class AuthorizationSubject:
    """授权判断所需的最小调用者信息。"""

    workspace_id: int
    is_system_admin: bool
    workspace_weight: int


@dataclass(frozen=True, slots=True)
class AuthorizationRequirement:
    """与接口框架无关的授权要求。"""

    roles: frozenset[str]
    resource_type: str | None
    resource_expression: str | None


class SqlbotPermission(BaseModel):
    """兼容现有装饰器声明格式的接口 DTO。"""

    role: list[str] | None = None
    type: str | None = None
    keyExpression: str | None = None

    def to_requirement(self) -> AuthorizationRequirement:
        return AuthorizationRequirement(
            roles=frozenset(self.role or ()),
            resource_type=self.type,
            resource_expression=self.keyExpression,
        )

