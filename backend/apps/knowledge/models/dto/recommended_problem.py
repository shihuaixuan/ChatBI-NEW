from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RecommendedProblemItem(BaseModel):
    """推荐问题的读取与写入 DTO。"""

    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    datasource_id: int | None = None
    question: str = ""
    remark: str = ""
    sort: int = 0
    create_time: datetime | None = None
    create_by: int | None = None


class RecommendedProblemResponse(BaseModel):
    """兼容现有前端的推荐问题配置响应。"""

    datasource_id: int | None = None
    recommended_config: int | None = None
    questions: str | None = None


class RecommendedProblemBase(BaseModel):
    """整批保存推荐问题的请求。"""

    datasource_id: int | None = None
    recommended_config: int | None = None
    problemInfo: list[RecommendedProblemItem] = Field(default_factory=list)
