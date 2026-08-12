"""Agent Trace 的稳定错误。"""


class TraceError(RuntimeError):
    """Trace 处理错误基类。"""


class TraceWriteError(TraceError):
    """持久化节点或详情失败。"""


class TraceContractError(TraceError, ValueError):
    """Trace 节点不满足公共契约。"""


__all__ = ["TraceContractError", "TraceError", "TraceWriteError"]
