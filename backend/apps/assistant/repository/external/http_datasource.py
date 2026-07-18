"""Assistant 外部数据源 HTTP 适配。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

import requests  # type: ignore[import-untyped]

from apps.assistant.errors import (
    AssistantConfigurationError,
    AssistantExternalDatasourceError,
)
from apps.assistant.models.dto import AssistantHeader
from apps.datasource import ExternalDatasource
from common.utils.aes_crypto import simple_aes_decrypt
from common.utils.utils import SQLBotLogUtil, string_to_numeric_hash


class AssistantOutDs:
    """读取一个高级 Assistant 暴露的外部数据源目录。"""

    def __init__(self, assistant: AssistantHeader) -> None:
        self.assistant = assistant
        self.certificate = assistant.certificate
        self.request_origin = assistant.request_origin
        self.ds_list = self._get_ds_from_api()

    def _get_ds_from_api(self) -> list[ExternalDatasource]:
        configuration = self._configuration()
        endpoint_value = configuration.get("endpoint")
        if not isinstance(endpoint_value, str) or not endpoint_value.strip():
            raise AssistantConfigurationError("EXTERNAL_ENDPOINT_REQUIRED")
        endpoint = self._get_complete_endpoint(endpoint_value)
        if endpoint is None:
            raise AssistantConfigurationError("EXTERNAL_ENDPOINT_DOMAIN_REQUIRED")

        headers, cookies, params = self._request_credentials()
        timeout_value = configuration.get("timeout", 10)
        if not isinstance(timeout_value, (int, str)) or isinstance(timeout_value, bool):
            raise AssistantConfigurationError("EXTERNAL_TIMEOUT_INVALID")
        try:
            timeout = int(timeout_value)
        except (TypeError, ValueError) as exc:
            raise AssistantConfigurationError("EXTERNAL_TIMEOUT_INVALID") from exc
        if timeout <= 0:
            raise AssistantConfigurationError("EXTERNAL_TIMEOUT_INVALID")

        try:
            response = requests.get(
                url=endpoint,
                params=params,
                headers=headers,
                cookies=cookies,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise AssistantExternalDatasourceError(
                f"Failed to get datasource list from {endpoint}: {exc}"
            ) from exc
        if response.status_code != 200:
            SQLBotLogUtil.error(
                f"Failed to get datasource list from {endpoint}, response: {response}"
            )
            raise AssistantExternalDatasourceError(
                f"Failed to get datasource list from {endpoint}, response: {response}"
            )

        try:
            result = response.json()
        except requests.JSONDecodeError as exc:
            raise AssistantExternalDatasourceError(
                f"Failed to parse datasource list from {endpoint}"
            ) from exc
        if not isinstance(result, dict):
            raise AssistantExternalDatasourceError(
                f"Failed to get datasource list from {endpoint}, error: invalid response"
            )
        if result.get("code") not in (0, 200):
            raise AssistantExternalDatasourceError(
                f"Failed to get datasource list from {endpoint}, error: {result.get('message')}"
            )
        raw_items = result.get("data", [])
        if not isinstance(raw_items, list) or not all(
            isinstance(item, dict) for item in raw_items
        ):
            raise AssistantExternalDatasourceError(
                f"Failed to get datasource list from {endpoint}, error: invalid data"
            )
        return [self._convert_to_schema(item, configuration) for item in raw_items]

    def _configuration(self) -> dict[str, object]:
        if not self.assistant.configuration:
            raise AssistantConfigurationError("EXTERNAL_CONFIGURATION_REQUIRED")
        try:
            configuration = json.loads(self.assistant.configuration)
        except json.JSONDecodeError as exc:
            raise AssistantConfigurationError("JSON_FORMAT") from exc
        if not isinstance(configuration, dict):
            raise AssistantConfigurationError("ROOT_MUST_BE_OBJECT")
        return configuration

    def _request_credentials(
        self,
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        if not self.certificate:
            raise AssistantConfigurationError("EXTERNAL_CERTIFICATE_REQUIRED")
        try:
            credentials = json.loads(self.certificate)
        except json.JSONDecodeError as exc:
            raise AssistantConfigurationError("EXTERNAL_CERTIFICATE_JSON") from exc
        if not isinstance(credentials, list):
            raise AssistantConfigurationError("EXTERNAL_CERTIFICATE_LIST")

        headers: dict[str, str] = {}
        cookies: dict[str, str] = {}
        params: dict[str, str] = {}
        targets = {"header": headers, "cookie": cookies, "param": params}
        for credential in credentials:
            if not isinstance(credential, dict):
                raise AssistantConfigurationError("EXTERNAL_CERTIFICATE_ITEM")
            target = credential.get("target")
            key = credential.get("key")
            value = credential.get("value")
            if (
                target not in targets
                or not isinstance(key, str)
                or not isinstance(value, str)
            ):
                raise AssistantConfigurationError("EXTERNAL_CERTIFICATE_ITEM")
            targets[target][key] = value
        return headers, cookies, params

    @staticmethod
    def _first_domain(text: str) -> str:
        return re.split(r"[,;]", text.strip())[0].strip()

    def _get_complete_endpoint(self, endpoint: str) -> str | None:
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        domain_text = self.assistant.domain
        if not domain_text:
            return None
        domain = (
            self.request_origin
            if self.request_origin
            else self._first_domain(domain_text)
        )
        return f"{domain.rstrip('/')}/{endpoint.lstrip('/')}"

    def get_simple_ds_list(self) -> list[dict[str, object]]:
        return [
            {
                "id": datasource.id,
                "name": datasource.name,
                "description": datasource.comment,
            }
            for datasource in self.ds_list
        ]

    def get_db_schema(
        self,
        datasource_id: int,
        question: str = "",
        embedding: bool = True,
        table_list: list[str] | None = None,
    ) -> str:
        del question, embedding
        datasource = self.get_ds(datasource_id)
        database_name = datasource.db_schema or datasource.dataBase
        schema = f"【DB_ID】 {database_name}\n【Schema】\n"
        tables: list[str] = []
        for table in datasource.tables or []:
            if table_list is not None and table.name not in table_list:
                continue
            table_name = table.name or ""
            table_prefix = (
                f"# Table: {database_name}.{table_name}"
                if datasource.type not in {"mysql", "es"}
                else f"# Table: {table_name}"
            )
            table_text = table_prefix
            if table.comment:
                table_text += f", {table.comment}\n[\n"
            else:
                table_text += "\n[\n"
            fields = []
            for field in table.fields or []:
                if field.comment:
                    fields.append(f"({field.name}:{field.type}, {field.comment})")
                else:
                    fields.append(f"({field.name}:{field.type})")
            table_text += ",\n".join(fields)
            table_text += "\n]\n"
            tables.append(table_text)
        return schema + "".join(tables)

    def get_ds(
        self,
        datasource_id: int,
        trans: Callable[..., str] | None = None,
    ) -> ExternalDatasource:
        for datasource in self.ds_list:
            if datasource.id == datasource_id:
                return datasource
        if trans is None:
            raise AssistantExternalDatasourceError(
                f"Datasource id {datasource_id} is not found."
            )
        translated = trans(
            "i18n_data_training.datasource_id_not_found",
            key=datasource_id,
        )
        raise AssistantExternalDatasourceError(str(translated))

    def _convert_to_schema(
        self,
        datasource_payload: dict[str, object],
        configuration: dict[str, object],
    ) -> ExternalDatasource:
        payload = dict(datasource_payload)
        if configuration.get("encrypt", False):
            key = configuration.get("aes_key")
            iv = configuration.get("aes_iv")
            for attribute in (
                "host",
                "user",
                "password",
                "dataBase",
                "db_schema",
                "schema",
                "mode",
            ):
                value = payload.get(attribute)
                if value:
                    if not isinstance(value, str):
                        raise AssistantExternalDatasourceError(
                            f"Invalid encrypted {attribute} for datasource "
                            f"{payload.get('name')}"
                        )
                    key_value = key if isinstance(key, str) else None
                    iv_value = iv if isinstance(iv, str) else None
                    try:
                        payload[attribute] = simple_aes_decrypt(
                            value,
                            key_value,
                            iv_value,
                        )
                    except Exception as exc:
                        raise AssistantExternalDatasourceError(
                            f"Failed to decrypt {attribute} for datasource "
                            f"{payload.get('name')}: {exc}"
                        ) from exc

        datasource_id = payload.get("id")
        if not datasource_id:
            marker_attributes = (
                "name",
                "type",
                "host",
                "port",
                "user",
                "dataBase",
                "schema",
                "mode",
            )
            marker = "".join(
                f"{payload.get(attribute, '')}--sqlbot--"
                for attribute in marker_attributes
                if attribute in payload
            )
            datasource_id = string_to_numeric_hash(marker)
        database_schema = payload.get("schema", payload.get("db_schema", ""))
        payload.pop("schema", None)
        return ExternalDatasource.model_validate(
            {**payload, "id": datasource_id, "db_schema": database_schema}
        )


class AssistantOutDsFactory:
    @staticmethod
    def get_instance(assistant: AssistantHeader) -> AssistantOutDs:
        return AssistantOutDs(assistant)
