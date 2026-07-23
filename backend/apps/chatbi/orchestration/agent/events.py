import orjson

from apps.chatbi.models.dto.agent import AgentEventPayload


def sse_event(payload: AgentEventPayload | dict) -> str:
    data = payload.model_dump() if isinstance(payload, AgentEventPayload) else payload
    return "data:" + orjson.dumps(data).decode() + "\n\n"
