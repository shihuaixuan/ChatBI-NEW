class SemanticError(RuntimeError):
    """语义模块公共错误基类。"""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class SemanticNotFoundError(SemanticError):
    """请求的语义资源不存在或不可见。"""


class SemanticForbiddenError(SemanticError):
    """当前租户无权访问目标资源。"""


class SemanticValidationError(SemanticError):
    """语义层业务校验失败。"""


class SemanticDataAccessError(SemanticError):
    """语义数据访问失败。"""
