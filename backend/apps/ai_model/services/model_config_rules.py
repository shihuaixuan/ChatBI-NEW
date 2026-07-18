"""AI 模型扩展参数的统一校验规则。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from apps.ai_model.errors import AIModelConfigInvalidError
from common.utils.utils import prepare_model_arg


def normalize_config_items(
    raw_items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """校验参数结构与键唯一性，并返回不修改输入的规范化副本。"""

    normalized_items: list[dict[str, Any]] = []
    keys: set[str] = set()
    for index, item in enumerate(raw_items, start=1):
        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            raise AIModelConfigInvalidError(f"KEY_REQUIRED:{index}")
        if "val" not in item:
            raise AIModelConfigInvalidError(f"VALUE_REQUIRED:{index}")
        key = key.strip()
        if key in keys:
            raise AIModelConfigInvalidError(f"DUPLICATE_KEY:{key}")
        keys.add(key)
        normalized_item = dict(item)
        normalized_item["key"] = key
        normalized_items.append(normalized_item)
    return normalized_items


def build_runtime_params(
    raw_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """把校验后的配置项转换为模型客户端参数。"""

    return {
        item["key"]: (
            "" if item["val"] == "" else prepare_model_arg(item["val"])
        )
        for item in normalize_config_items(raw_items)
    }
