from langchain_core.messages import AIMessageChunk

from apps.ai_model.streaming import process_stream


def test_process_stream_reads_native_reasoning_and_token_usage():
    token_usage: dict[str, int] = {}
    chunks = iter(
        [
            AIMessageChunk(
                content="答案",
                additional_kwargs={"reasoning_content": "分析"},
                usage_metadata={
                    "input_tokens": 2,
                    "output_tokens": 1,
                    "total_tokens": 3,
                },
            )
        ]
    )

    stream = process_stream(chunks, token_usage)
    first_chunk = next(stream)

    assert first_chunk == {"content": "答案", "reasoning_content": "分析"}
    assert token_usage == {
        "input_tokens": 2,
        "output_tokens": 1,
        "total_tokens": 3,
    }
    assert list(stream) == []


def test_process_stream_parses_reasoning_tags_split_across_chunks():
    chunks = iter(
        [
            AIMessageChunk(content="<thi"),
            AIMessageChunk(content="nk>分析"),
            AIMessageChunk(content="完成</thi"),
            AIMessageChunk(content="nk>答案"),
        ]
    )

    result = list(process_stream(chunks))

    assert "".join(chunk["content"] for chunk in result) == "答案"
    assert result[1]["reasoning_content"] == "分析"
    assert result[2]["reasoning_content"] == "完成"
    assert result[3]["reasoning_content"] == ""
