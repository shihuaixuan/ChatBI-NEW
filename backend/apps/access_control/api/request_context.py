"""授权装饰器使用的请求上下文。"""

from contextvars import ContextVar, Token

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


class RequestContext:
    _current_request: ContextVar[Request] = ContextVar("_current_request")

    @classmethod
    def set_request(cls, request: Request) -> Token[Request]:
        return cls._current_request.set(request)

    @classmethod
    def get_request(cls) -> Request:
        try:
            return cls._current_request.get()
        except LookupError as exc:
            raise RuntimeError(
                "No request context found. "
                "Make sure RequestContextMiddleware is installed."
            ) from exc

    @classmethod
    def reset(cls, token: Token[Request]) -> None:
        cls._current_request.reset(token)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        token = RequestContext.set_request(request)
        try:
            return await call_next(request)
        finally:
            RequestContext.reset(token)

