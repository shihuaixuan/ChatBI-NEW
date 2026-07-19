from __future__ import annotations

from collections.abc import Iterator

from langchain_core.messages import BaseMessageChunk

from common.core.config import settings
from common.utils.utils import SQLBotLogUtil


def get_token_usage(
    chunk: BaseMessageChunk,
    token_usage: dict[str, int] | None = None,
) -> None:
    """把当前模型分块中的用量快照写入累计结果。"""

    usage_metadata = getattr(chunk, "usage_metadata", None)
    if token_usage is None or not isinstance(usage_metadata, dict):
        return
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage_metadata.get(key)
        if isinstance(value, int):
            token_usage[key] = value


def process_stream(
    res: Iterator[BaseMessageChunk],
    token_usage: dict[str, int] | None = None,
    enable_tag_parsing: bool = settings.PARSE_REASONING_BLOCK_ENABLED,
    start_tag: str = settings.DEFAULT_REASONING_CONTENT_START,
    end_tag: str = settings.DEFAULT_REASONING_CONTENT_END,
) -> Iterator[dict[str, str]]:
    """统一解析模型正文、思考内容和 token 用量。"""

    if token_usage is None:
        token_usage = {}
    in_thinking_block = False
    native_reasoning_detected = False
    pending_start_tag = ""
    pending_end_tag = ""

    for chunk in res:
        SQLBotLogUtil.info(str(chunk))
        reasoning_content_chunk = ""
        raw_content = chunk.content
        if not isinstance(raw_content, str):
            raise TypeError("MODEL_STREAM_CONTENT_MUST_BE_STRING")
        content = raw_content
        output_content = ""

        if "reasoning_content" in chunk.additional_kwargs:
            reasoning_content = chunk.additional_kwargs.get(
                "reasoning_content",
                "",
            )
            if reasoning_content is None:
                reasoning_content = ""
            if not isinstance(reasoning_content, str):
                raise TypeError("MODEL_REASONING_CONTENT_MUST_BE_STRING")
            reasoning_content_chunk = reasoning_content
            native_reasoning_detected = (
                native_reasoning_detected or bool(reasoning_content.strip())
            )

        # 模型已提供独立思考字段时，不再重复解析正文标签。
        if not in_thinking_block and native_reasoning_detected:
            get_token_usage(chunk, token_usage)
            yield {
                "content": content,
                "reasoning_content": reasoning_content_chunk,
            }
            continue

        if pending_start_tag:
            content = pending_start_tag + content
            pending_start_tag = ""

        if enable_tag_parsing and not in_thinking_block and start_tag:
            if start_tag in content:
                start_index = content.index(start_tag)
                if start_index == 0 or not content[:start_index].strip():
                    output_content += content[:start_index]
                    content = content[start_index + len(start_tag) :]
                    in_thinking_block = True
                else:
                    output_content += content
                    content = ""
            else:
                for index in range(1, len(start_tag)):
                    if content.endswith(start_tag[:index]):
                        if not content[:-index].strip():
                            pending_start_tag = start_tag[:index]
                            content = content[:-index]
                            output_content += content
                            content = ""
                        break

        if enable_tag_parsing and in_thinking_block and end_tag:
            if pending_end_tag:
                content = pending_end_tag + content
                pending_end_tag = ""
            if end_tag in content:
                end_index = content.index(end_tag)
                reasoning_content_chunk += content[:end_index]
                content = content[end_index + len(end_tag) :]
                in_thinking_block = False
                output_content += content
            else:
                pending_length = 0
                for index in range(1, len(end_tag)):
                    if content.endswith(end_tag[:index]):
                        pending_length = index
                if pending_length:
                    pending_end_tag = content[-pending_length:]
                    content = content[:-pending_length]
                reasoning_content_chunk += content
        else:
            output_content += content

        get_token_usage(chunk, token_usage)
        yield {
            "content": output_content,
            "reasoning_content": reasoning_content_chunk,
        }


__all__ = ["get_token_usage", "process_stream"]
