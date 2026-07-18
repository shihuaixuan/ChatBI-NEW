"""API Key SQLModel 仓储。"""

from sqlmodel import Session, col, func, select

from apps.access_control.models.dto import ApiKeyRecord
from apps.access_control.models.orm import ApiKeyModel, UserModel


class SQLModelApiKeyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _record(api_key: ApiKeyModel) -> ApiKeyRecord:
        return ApiKeyRecord.model_validate(api_key.model_dump())

    def list_api_keys(self, user_id: int) -> list[ApiKeyRecord]:
        api_keys = self._session.exec(
            select(ApiKeyModel)
            .where(col(ApiKeyModel.uid) == user_id)
            .order_by(col(ApiKeyModel.create_time).desc())
        ).all()
        return [self._record(api_key) for api_key in api_keys]

    def get_api_key(self, api_key_id: int) -> ApiKeyRecord | None:
        api_key = self._session.get(ApiKeyModel, api_key_id)
        return self._record(api_key) if api_key else None

    def get_api_key_by_access_key(self, access_key: str) -> ApiKeyRecord | None:
        api_key = self._session.exec(
            select(ApiKeyModel).where(col(ApiKeyModel.access_key) == access_key)
        ).first()
        return self._record(api_key) if api_key else None

    def create_api_key(
        self,
        *,
        user_id: int,
        access_key: str,
        secret_key: str,
        create_time: int,
        limit: int,
    ) -> ApiKeyRecord | None:
        # 锁定用户行，使同一用户的数量检查和新增串行化。
        user = self._session.exec(
            select(UserModel)
            .where(col(UserModel.id) == user_id)
            .with_for_update()
        ).first()
        if user is None:
            raise RuntimeError(f"ACCESS_CONTROL_API_KEY_USER_NOT_FOUND:{user_id}")
        count = self._session.exec(
            select(func.count())
            .select_from(ApiKeyModel)
            .where(col(ApiKeyModel.uid) == user_id)
        ).one()
        if count >= limit:
            return None

        api_key = ApiKeyModel(
            access_key=access_key,
            secret_key=secret_key,
            create_time=create_time,
            uid=user_id,
            status=True,
        )
        self._session.add(api_key)
        self._session.commit()
        self._session.refresh(api_key)
        return self._record(api_key)

    def update_api_key_status(
        self,
        api_key_id: int,
        status: bool,
    ) -> ApiKeyRecord | None:
        api_key = self._session.get(ApiKeyModel, api_key_id)
        if api_key is None:
            return None
        if api_key.status != status:
            api_key.status = status
            self._session.add(api_key)
            self._session.commit()
            self._session.refresh(api_key)
        return self._record(api_key)

    def delete_api_key(self, api_key_id: int) -> ApiKeyRecord | None:
        api_key = self._session.get(ApiKeyModel, api_key_id)
        if api_key is None:
            return None
        record = self._record(api_key)
        self._session.delete(api_key)
        self._session.commit()
        return record
