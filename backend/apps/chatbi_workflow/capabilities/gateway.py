from typing import Any, Protocol


class ChatBICapabilityGateway(Protocol):
    """ChatBI 节点调用底层能力的唯一防腐层。"""

    def invoke(
        self,
        capability: str,
        request: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]: ...
