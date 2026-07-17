"""Semantic 批量 embedding provider 契约测试。"""

from __future__ import annotations

import apps.retrieval.embedding as retrieval_embedding
from apps.retrieval.embedding import (
    OpenAICompatibleEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "data": [
                {"index": 1, "embedding": [0.3, 0.4]},
                {"index": 0, "embedding": [0.1, 0.2]},
            ]
        }


def test_openai_compatible_provider_sends_one_batch_and_restores_input_order(monkeypatch):
    requests: list[dict] = []

    def fake_post(_url, *, headers, json, timeout):
        requests.append({"headers": headers, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(retrieval_embedding.httpx, "post", fake_post)
    provider = OpenAICompatibleEmbeddingProvider(
        api_base_url="https://embedding.example/v1",
        api_key="secret",
        model="BAAI/bge-m3",
        dimension=2,
        timeout=5,
    )

    vectors = provider.embed_documents(["第一个文本", "第二个文本"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert requests == [
        {
            "headers": {"Authorization": "Bearer secret"},
            "json": {"model": "BAAI/bge-m3", "input": ["第一个文本", "第二个文本"]},
            "timeout": 5,
        }
    ]


def test_openai_compatible_query_reuses_batch_contract(monkeypatch):
    provider = OpenAICompatibleEmbeddingProvider(
        api_base_url="https://embedding.example/v1",
        api_key="secret",
        model="BAAI/bge-m3",
        dimension=2,
    )
    calls: list[list[str]] = []

    def fake_embed_documents(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.5, 0.6]]

    monkeypatch.setattr(provider, "embed_documents", fake_embed_documents)

    assert provider.embed_query("销售额") == [0.5, 0.6]
    assert calls == [["销售额"]]


def test_sentence_transformer_provider_uses_cached_model_batch_contract(monkeypatch):
    calls: list[dict] = []

    class _Encoded:
        def tolist(self):
            return [[0.1, 0.2], [0.3, 0.4]]

    class _Model:
        def encode(self, texts, **kwargs):
            calls.append({"texts": texts, **kwargs})
            return _Encoded()

    monkeypatch.setattr(retrieval_embedding, "_sentence_transformer_model", lambda _name: _Model())
    provider = SentenceTransformerEmbeddingProvider(model="BAAI/bge-m3", dimension=2)

    vectors = provider.embed_documents(["第一个文本", "第二个文本"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert calls == [
        {
            "texts": ["第一个文本", "第二个文本"],
            "normalize_embeddings": True,
            "show_progress_bar": False,
            "batch_size": 2,
        }
    ]
