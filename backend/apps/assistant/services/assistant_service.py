"""Assistant 配置、范围和公开数据源业务服务。"""

from __future__ import annotations

import json
from collections.abc import Callable

from apps.assistant.errors import (
    AssistantConfigurationError,
    AssistantCustomModelError,
    AssistantDatasourceScopeError,
    AssistantNotFoundError,
    AssistantWorkspaceMismatchError,
)
from apps.assistant.models.dto import (
    AssistantBase,
    AssistantCreateData,
    AssistantDTO,
    AssistantHeader,
    AssistantPublicInfo,
    AssistantRecord,
    AssistantReference,
    AssistantUiSchema,
    AssistantUiUpdateResult,
    AssistantUpdateData,
)
from apps.assistant.repository import AssistantRepository, ExternalDatasourceCatalog
from apps.datasource import (
    DatasourceCatalog,
    DatasourceSummary,
    get_database_type_name,
)
from common.utils.time import get_timestamp
from common.utils.utils import get_domain_list, origin_match_domain

LOCAL_DATASOURCE_ASSISTANT_TYPES = frozenset({0, 2})
EXTERNAL_DATASOURCE_ASSISTANT_TYPES = frozenset({1, 3})
PAGE_EMBEDDED_ASSISTANT_TYPE = 4
VALID_ASSISTANT_TYPES = frozenset({0, 1, 2, 3, 4})

ModelReferenceChecker = Callable[[int], bool]
SecretGenerator = Callable[[], str]
ExternalDatasourceFactory = Callable[[AssistantHeader], ExternalDatasourceCatalog]


class AssistantService:
    def __init__(
        self,
        repository: AssistantRepository,
        datasource_catalog: DatasourceCatalog,
        *,
        model_exists: ModelReferenceChecker,
        generate_app_id: SecretGenerator,
        generate_app_secret: SecretGenerator,
        external_datasource_factory: ExternalDatasourceFactory,
    ) -> None:
        self._repository = repository
        self._datasource_catalog = datasource_catalog
        self._model_exists = model_exists
        self._generate_app_id = generate_app_id
        self._generate_app_secret = generate_app_secret
        self._external_datasource_factory = external_datasource_factory

    def get(self, assistant_id: int) -> AssistantRecord:
        assistant = self._repository.get(assistant_id)
        if assistant is None:
            raise AssistantNotFoundError(assistant_id)
        return assistant

    def get_by_app_id(self, app_id: str) -> AssistantRecord:
        if not app_id:
            raise AssistantNotFoundError(app_id)
        assistant = self._repository.get_by_app_id(app_id)
        if assistant is None:
            raise AssistantNotFoundError(app_id)
        return assistant

    def get_for_workspace(
        self,
        assistant_id: int,
        current_workspace_id: int,
    ) -> AssistantRecord:
        assistant = self.get(assistant_id)
        self._require_workspace(assistant, current_workspace_id)
        return assistant

    def get_public(self, assistant_id: int) -> AssistantPublicInfo:
        return AssistantPublicInfo.model_validate(self.get(assistant_id).model_dump())

    def get_public_by_app_id(self, app_id: str) -> AssistantPublicInfo:
        return AssistantPublicInfo.model_validate(
            self.get_by_app_id(app_id).model_dump()
        )

    def get_header(self, assistant_id: int) -> AssistantHeader:
        return AssistantHeader.model_validate(self.get(assistant_id).model_dump())

    def list_for_workspace(self, workspace_id: int) -> list[AssistantRecord]:
        return self._repository.list_for_workspace(
            workspace_id,
            exclude_type=PAGE_EMBEDDED_ASSISTANT_TYPE,
        )

    def list_advanced_for_workspace(
        self,
        workspace_id: int,
    ) -> list[AssistantRecord]:
        return self._repository.list_for_workspace(
            workspace_id,
            assistant_type=1,
        )

    def list_references(
        self,
        assistant_ids: list[int] | None = None,
        *,
        workspace_id: int | None = None,
        assistant_type: int | None = None,
    ) -> list[AssistantReference]:
        return self._repository.list_references(
            assistant_ids,
            workspace_id=workspace_id,
            assistant_type=assistant_type,
        )

    def create(
        self,
        creator: AssistantBase,
        current_workspace_id: int,
    ) -> AssistantRecord:
        workspace_id = (
            1 if creator.type == PAGE_EMBEDDED_ASSISTANT_TYPE else current_workspace_id
        )
        configuration, custom_model = self._validate_configuration_and_model(
            assistant_type=creator.type,
            configuration=creator.configuration,
            workspace_id=workspace_id,
            enable_custom_model=bool(creator.enable_custom_model),
            custom_model=creator.custom_model,
        )
        app_id = None
        app_secret = None
        if creator.type == PAGE_EMBEDDED_ASSISTANT_TYPE:
            app_id = self._generate_app_id()
            app_secret = self._generate_app_secret()
        return self._repository.create(
            AssistantCreateData(
                name=creator.name,
                domain=creator.domain,
                type=creator.type,
                configuration=configuration,
                description=creator.description,
                create_time=get_timestamp(),
                app_id=app_id,
                app_secret=app_secret,
                oid=workspace_id,
                enable_custom_model=bool(creator.enable_custom_model),
                custom_model=custom_model,
            )
        )

    def update(
        self,
        editor: AssistantDTO,
        current_workspace_id: int,
    ) -> AssistantRecord:
        current = self.get(editor.id)
        self._require_workspace(current, current_workspace_id)
        target_workspace_id = (
            1 if editor.type == PAGE_EMBEDDED_ASSISTANT_TYPE else current_workspace_id
        )
        configuration, custom_model = self._validate_configuration_and_model(
            assistant_type=editor.type,
            configuration=editor.configuration,
            workspace_id=target_workspace_id,
            enable_custom_model=bool(editor.enable_custom_model),
            custom_model=editor.custom_model,
        )
        updated = self._repository.update(
            editor.id,
            AssistantUpdateData(
                name=editor.name,
                domain=editor.domain,
                type=editor.type,
                configuration=configuration,
                description=editor.description,
                oid=target_workspace_id,
                enable_custom_model=bool(editor.enable_custom_model),
                custom_model=custom_model,
            ),
        )
        if updated is None:
            raise AssistantNotFoundError(editor.id)
        return updated

    def delete(
        self,
        assistant_id: int,
        current_workspace_id: int,
    ) -> AssistantRecord:
        current = self.get(assistant_id)
        self._require_workspace(current, current_workspace_id)
        deleted = self._repository.delete(assistant_id)
        if deleted is None:
            raise AssistantNotFoundError(assistant_id)
        return deleted

    def update_ui(
        self,
        schema: AssistantUiSchema,
        uploaded_asset_ids: dict[str, str],
        current_workspace_id: int,
    ) -> AssistantUiUpdateResult:
        assistant = self.get_for_workspace(schema.id, current_workspace_id)
        configuration = self._parse_configuration(
            assistant.configuration,
            required=False,
        )
        explicit_fields = schema.model_dump(exclude_unset=True)
        explicit_fields.pop("id", None)
        obsolete_asset_ids: list[str] = []

        for asset_field in ("logo", "float_icon"):
            current_asset_id = configuration.get(asset_field)
            uploaded_asset_id = uploaded_asset_ids.get(asset_field)
            if uploaded_asset_id:
                if isinstance(current_asset_id, str) and current_asset_id:
                    obsolete_asset_ids.append(current_asset_id)
                configuration[asset_field] = uploaded_asset_id
                explicit_fields.pop(asset_field, None)
            elif asset_field in explicit_fields:
                next_asset_id = explicit_fields.pop(asset_field)
                if not next_asset_id and isinstance(current_asset_id, str):
                    obsolete_asset_ids.append(current_asset_id)
                configuration[asset_field] = next_asset_id

        configuration.update(explicit_fields)
        updated = self._repository.update_configuration(
            schema.id,
            json.dumps(configuration, ensure_ascii=False),
        )
        if updated is None:
            raise AssistantNotFoundError(schema.id)
        return AssistantUiUpdateResult(
            assistant=updated,
            obsolete_asset_ids=obsolete_asset_ids,
        )

    def list_datasources(
        self,
        assistant: AssistantHeader,
    ) -> list[DatasourceSummary]:
        if assistant.type in LOCAL_DATASOURCE_ASSISTANT_TYPES:
            configuration = self._parse_configuration(
                assistant.configuration,
                required=True,
            )
            configured_workspace_id = self._configuration_workspace_id(configuration)
            if assistant.oid is None or assistant.oid <= 0:
                raise AssistantConfigurationError("WORKSPACE_REQUIRED")
            workspace_id = int(assistant.oid)
            if configured_workspace_id != workspace_id:
                raise AssistantConfigurationError("WORKSPACE_MISMATCH")
            datasource_ids = None
            if not assistant.online:
                datasource_ids = self._normalize_datasource_ids(
                    configuration.get("public_list"),
                    "PUBLIC_LIST",
                )
                if not datasource_ids:
                    return []
            return self._datasource_catalog.list_for_workspace(
                workspace_id,
                datasource_ids,
            )

        if assistant.type in EXTERNAL_DATASOURCE_ASSISTANT_TYPES:
            catalog = self.build_external_datasource_catalog(assistant)
            return [
                DatasourceSummary(
                    id=str(datasource.id),
                    name=datasource.name,
                    description=datasource.description or datasource.comment,
                    type=datasource.type,
                    type_name=get_database_type_name(datasource.type),
                    num=len(datasource.tables or []),
                )
                for datasource in catalog.ds_list
                if get_database_type_name(datasource.type)
            ]

        return []

    def build_external_datasource_catalog(
        self,
        assistant: AssistantHeader,
    ) -> ExternalDatasourceCatalog:
        if assistant.type not in EXTERNAL_DATASOURCE_ASSISTANT_TYPES:
            raise AssistantConfigurationError("NOT_EXTERNAL_DATASOURCE_ASSISTANT")
        return self._external_datasource_factory(assistant)

    def list_allowed_origins(self) -> list[str]:
        seen: set[str] = set()
        origins: list[str] = []
        for domain_text in self._repository.list_domains():
            for domain in get_domain_list(domain_text):
                if domain not in seen:
                    seen.add(domain)
                    origins.append(domain)
        return origins

    @staticmethod
    def origin_is_allowed(origin: str, domain: str) -> bool:
        return origin_match_domain(origin.rstrip("/"), domain)

    @staticmethod
    def _require_workspace(
        assistant: AssistantRecord,
        current_workspace_id: int,
    ) -> None:
        if assistant.oid != current_workspace_id:
            raise AssistantWorkspaceMismatchError(
                assistant.id,
                current_workspace_id,
            )

    def _validate_configuration_and_model(
        self,
        *,
        assistant_type: int,
        configuration: str | None,
        workspace_id: int,
        enable_custom_model: bool,
        custom_model: str | None,
    ) -> tuple[str | None, str | None]:
        if assistant_type not in VALID_ASSISTANT_TYPES:
            raise AssistantConfigurationError("TYPE")

        normalized_configuration = configuration
        if assistant_type in LOCAL_DATASOURCE_ASSISTANT_TYPES:
            payload = self._parse_configuration(configuration, required=True)
            configured_workspace_id = self._configuration_workspace_id(payload)
            if configured_workspace_id != workspace_id:
                raise AssistantConfigurationError("WORKSPACE_MISMATCH")
            public_ids = self._normalize_datasource_ids(
                payload.get("public_list"),
                "PUBLIC_LIST",
            )
            private_ids = self._normalize_datasource_ids(
                payload.get("private_list"),
                "PRIVATE_LIST",
            )
            referenced_ids = sorted(set(public_ids + private_ids))
            self._require_datasource_scope(workspace_id, referenced_ids)
            payload["oid"] = workspace_id
            payload["public_list"] = public_ids
            if "private_list" in payload:
                payload["private_list"] = private_ids
            normalized_configuration = json.dumps(payload, ensure_ascii=False)
        elif assistant_type in EXTERNAL_DATASOURCE_ASSISTANT_TYPES:
            payload = self._parse_configuration(configuration, required=True)
            endpoint = payload.get("endpoint")
            if not isinstance(endpoint, str) or not endpoint.strip():
                raise AssistantConfigurationError("EXTERNAL_ENDPOINT_REQUIRED")
            normalized_configuration = json.dumps(payload, ensure_ascii=False)
        elif configuration:
            payload = self._parse_configuration(configuration, required=False)
            normalized_configuration = json.dumps(payload, ensure_ascii=False)

        normalized_custom_model = custom_model
        if enable_custom_model:
            if custom_model is None or not str(custom_model).strip():
                raise AssistantCustomModelError(custom_model)
            try:
                model_id = int(custom_model)
            except (TypeError, ValueError) as exc:
                raise AssistantCustomModelError(custom_model) from exc
            if not self._model_exists(model_id):
                raise AssistantCustomModelError(custom_model)
            normalized_custom_model = str(model_id)
        return normalized_configuration, normalized_custom_model

    def _require_datasource_scope(
        self,
        workspace_id: int,
        datasource_ids: list[int],
    ) -> None:
        if not datasource_ids:
            return
        available_ids = {
            int(datasource.id)
            for datasource in self._datasource_catalog.list_for_workspace(
                workspace_id,
                datasource_ids,
            )
        }
        missing_ids = sorted(set(datasource_ids) - available_ids)
        if missing_ids:
            raise AssistantDatasourceScopeError(missing_ids)

    @staticmethod
    def _parse_configuration(
        configuration: str | None,
        *,
        required: bool,
    ) -> dict[str, object]:
        if not configuration:
            if required:
                raise AssistantConfigurationError("REQUIRED")
            return {}
        try:
            payload = json.loads(configuration)
        except json.JSONDecodeError as exc:
            raise AssistantConfigurationError("JSON_FORMAT") from exc
        if not isinstance(payload, dict):
            raise AssistantConfigurationError("ROOT_MUST_BE_OBJECT")
        return payload

    @staticmethod
    def _configuration_workspace_id(configuration: dict[str, object]) -> int:
        workspace_value = configuration.get("oid")
        if not isinstance(workspace_value, (int, str)) or isinstance(
            workspace_value, bool
        ):
            raise AssistantConfigurationError("WORKSPACE_REQUIRED")
        try:
            workspace_id = int(workspace_value)
        except (TypeError, ValueError) as exc:
            raise AssistantConfigurationError("WORKSPACE_REQUIRED") from exc
        if workspace_id <= 0:
            raise AssistantConfigurationError("WORKSPACE_REQUIRED")
        return workspace_id

    @staticmethod
    def _normalize_datasource_ids(
        raw_ids: object,
        field_name: str,
    ) -> list[int]:
        if raw_ids is None:
            return []
        if not isinstance(raw_ids, list):
            raise AssistantConfigurationError(f"{field_name}_MUST_BE_LIST")
        normalized: list[int] = []
        for raw_id in raw_ids:
            if not isinstance(raw_id, (int, str)) or isinstance(raw_id, bool):
                raise AssistantConfigurationError(f"{field_name}_ID")
            try:
                datasource_id = int(raw_id)
            except (TypeError, ValueError) as exc:
                raise AssistantConfigurationError(f"{field_name}_ID") from exc
            if datasource_id <= 0:
                raise AssistantConfigurationError(f"{field_name}_ID")
            if datasource_id not in normalized:
                normalized.append(datasource_id)
        return normalized
