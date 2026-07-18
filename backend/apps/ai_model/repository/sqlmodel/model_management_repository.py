"""AI 模型管理的 SQLModel 仓储实现。"""

from sqlmodel import Session, col, func, select, update

from apps.ai_model.models.dto import (
    AIModelCreateData,
    AIModelDeleteResult,
    AIModelRecord,
    AIModelSecretUpdate,
    AIModelUpdateData,
)
from apps.ai_model.models.orm import AiModelDetail
from apps.ai_model.repository import AIModelManagementRepository


class SQLModelAIModelManagementRepository(AIModelManagementRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_models(self, keyword: str | None = None) -> list[AIModelRecord]:
        statement = select(AiModelDetail)
        if keyword:
            statement = statement.where(col(AiModelDetail.name).contains(keyword))
        statement = statement.order_by(
            col(AiModelDetail.default_model).desc(),
            col(AiModelDetail.name),
            col(AiModelDetail.create_time),
        )
        return [self._to_record(model) for model in self._session.exec(statement)]

    def get_model(self, model_id: int) -> AIModelRecord | None:
        model = self._session.get(AiModelDetail, model_id)
        return self._to_record(model) if model is not None else None

    def has_default(self) -> bool:
        model_id = self._session.exec(
            select(AiModelDetail.id).where(
                col(AiModelDetail.default_model).is_(True)
            )
        ).first()
        return model_id is not None

    def create_model(self, data: AIModelCreateData) -> AIModelRecord:
        model_count = self._session.exec(
            select(func.count()).select_from(AiModelDetail)
        ).one()
        make_default = model_count == 0 or data.default_model
        if make_default and model_count:
            self._session.exec(update(AiModelDetail).values(default_model=False))

        model_data = data.model_dump()
        model_data["default_model"] = make_default
        model = AiModelDetail.model_validate(model_data)
        self._session.add(model)
        self._session.commit()
        self._session.refresh(model)
        return self._to_record(model)

    def update_model(
        self,
        model_id: int,
        data: AIModelUpdateData,
    ) -> AIModelRecord | None:
        model = self._session.get(AiModelDetail, model_id)
        if model is None:
            return None
        model.sqlmodel_update(data.model_dump())
        self._session.add(model)
        self._session.commit()
        self._session.refresh(model)
        return self._to_record(model)

    def set_default(self, model_id: int) -> AIModelRecord | None:
        model = self._session.exec(
            select(AiModelDetail)
            .where(col(AiModelDetail.id) == model_id)
            .with_for_update()
        ).first()
        if model is None:
            return None
        if model.default_model:
            return self._to_record(model)

        self._session.exec(update(AiModelDetail).values(default_model=False))
        model.default_model = True
        self._session.add(model)
        self._session.commit()
        self._session.refresh(model)
        return self._to_record(model)

    def delete_model(self, model_id: int) -> AIModelDeleteResult:
        model = self._session.exec(
            select(AiModelDetail)
            .where(col(AiModelDetail.id) == model_id)
            .with_for_update()
        ).first()
        if model is None:
            return AIModelDeleteResult.NOT_FOUND
        if model.default_model:
            return AIModelDeleteResult.DEFAULT_MODEL
        self._session.delete(model)
        self._session.commit()
        return AIModelDeleteResult.DELETED

    def apply_secret_updates(self, updates: list[AIModelSecretUpdate]) -> None:
        if not updates:
            return
        for item in updates:
            model = self._session.get(AiModelDetail, item.model_id)
            if model is None:
                raise RuntimeError(f"AI_MODEL_NOT_FOUND_DURING_SECRET_MIGRATION:{item.model_id}")
            model.api_domain = item.api_domain
            model.api_key = item.api_key
            model.supplier = item.supplier
            self._session.add(model)
        self._session.commit()

    @staticmethod
    def _to_record(model: AiModelDetail) -> AIModelRecord:
        if model.id is None:
            raise RuntimeError("AI_MODEL_ID_MISSING")
        return AIModelRecord.model_validate(model)
