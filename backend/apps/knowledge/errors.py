class RecommendedProblemDatasourceNotFoundError(LookupError):
    """推荐问题关联的数据源不存在。"""

    def __init__(self, datasource_id: int) -> None:
        super().__init__(f"Datasource {datasource_id} not found")
        self.datasource_id = datasource_id


class RecommendedProblemRequestError(ValueError):
    """推荐问题保存请求缺少必要字段。"""
