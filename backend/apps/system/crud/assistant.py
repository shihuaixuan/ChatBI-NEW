"""Assistant 旧导入路径兼容转发；权威实现位于 apps.assistant。"""

from apps.assistant.public import (
    AssistantOutDs,
    AssistantOutDsFactory,
    get_assistant_ds,
    get_assistant_info,
    get_assistant_user,
    get_out_ds_conf,
    init_dynamic_cors,
)

__all__ = [
    "AssistantOutDs",
    "AssistantOutDsFactory",
    "get_assistant_ds",
    "get_assistant_info",
    "get_assistant_user",
    "get_out_ds_conf",
    "init_dynamic_cors",
]
