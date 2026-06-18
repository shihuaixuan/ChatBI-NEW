from typing import Any, Protocol


class CapabilityGateway(Protocol):
    """业务节点调用 SQL、检索、LLM 等能力的防腐层。"""

    def invoke(
        self,
        capability: str,
        request: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]: ...
