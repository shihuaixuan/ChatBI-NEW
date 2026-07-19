from datetime import datetime

from pydantic import BaseModel, Field


class RecommendedProblemItem(BaseModel):
    id: int | None = None
    datasource_id: int | None = None
    question: str = ""
    remark: str = ""
    sort: int = 0
    create_time: datetime | None = None
    create_by: int | None = None


class RecommendedProblemResponse:
    def __init__(
        self,
        datasource_id: int | None,
        recommended_config: int | None,
        questions: str | None,
    ) -> None:
        self.datasource_id = datasource_id
        self.recommended_config = recommended_config
        self.questions = questions

    datasource_id: int | None = None
    recommended_config: int | None = None
    questions: str | None = None


class RecommendedProblemBase(BaseModel):
    datasource_id: int | None = None
    recommended_config: int | None = None
    problemInfo: list[RecommendedProblemItem] = Field(default_factory=list)


class RecommendedProblemBaseChat:
    def __init__(self, content: list[str]) -> None:
        self.content = content

    content: list[str] = []
