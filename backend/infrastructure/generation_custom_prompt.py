from typing import Any, cast

from sqlbot_xpack.custom_prompt.curd.custom_prompt import (  # type: ignore[import-untyped]
    find_custom_prompts,
)
from sqlbot_xpack.custom_prompt.models.custom_prompt_model import (  # type: ignore[import-untyped]
    CustomPromptTypeEnum,
)
from sqlbot_xpack.license.license_manage import (  # type: ignore[import-untyped]
    SQLBotLicenseUtil,
)
from sqlmodel import Session

from apps.chatbi.models import (
    GenerationCustomPromptQuery,
    GenerationCustomPromptResult,
)
from apps.chatbi.services import GenerationCustomPromptService


class XPackGenerationCustomPromptProvider:
    """把 xpack 自定义提示词能力适配到 ChatBI 稳定端口。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def is_enabled(self) -> bool:
        return cast(bool, SQLBotLicenseUtil.valid())

    def find(
        self,
        query: GenerationCustomPromptQuery,
    ) -> GenerationCustomPromptResult:
        prompt_type = CustomPromptTypeEnum(query.prompt_type.value)
        prompt, items = cast(
            tuple[str, list[dict[str, Any]]],
            find_custom_prompts(
                self._session,
                prompt_type,
                cast(int, query.workspace_id),
                query.datasource_id,
            ),
        )
        return GenerationCustomPromptResult(prompt=prompt, items=items)


def build_generation_custom_prompt_service(
    session: Session,
) -> GenerationCustomPromptService:
    """装配旧 Chat 使用的自定义提示词查询服务。"""

    return GenerationCustomPromptService(
        XPackGenerationCustomPromptProvider(session)
    )


__all__ = [
    "XPackGenerationCustomPromptProvider",
    "build_generation_custom_prompt_service",
]
