"""Swagger i18n 旧入口，仅用于兼容外部扩展。"""

from common.interfaces.i18n import (
    DEFAULT_LANG,
    LOCALES_DIR,
    PLACEHOLDER_PREFIX,
    get_translation,
    i18n_list,
    load_translation,
    tags_metadata,
)

__all__ = [
    "DEFAULT_LANG",
    "LOCALES_DIR",
    "PLACEHOLDER_PREFIX",
    "get_translation",
    "i18n_list",
    "load_translation",
    "tags_metadata",
]
