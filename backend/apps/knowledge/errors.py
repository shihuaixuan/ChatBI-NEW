class RecommendedProblemDatasourceNotFoundError(LookupError):
    """推荐问题关联的数据源不存在。"""

    def __init__(self, datasource_id: int) -> None:
        super().__init__(f"Datasource {datasource_id} not found")
        self.datasource_id = datasource_id


class RecommendedProblemRequestError(ValueError):
    """推荐问题保存请求缺少必要字段。"""


class SQLExampleError(ValueError):
    """可由接口层本地化的 SQL 示例业务错误。"""

    def __init__(self, message_key: str, *format_args: object) -> None:
        super().__init__(message_key)
        self.message_key = message_key
        self.format_args = format_args


class SQLExampleNotFoundError(SQLExampleError):
    def __init__(self) -> None:
        super().__init__("i18n_data_training.data_training_not_exists")


class SQLExampleDuplicateError(SQLExampleError):
    def __init__(self) -> None:
        super().__init__("i18n_data_training.exists_in_db")
