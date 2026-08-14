"""统一检索候选重排序 Provider。"""

from __future__ import annotations

from typing import Any

import httpx

from apps.retrieval.errors import RetrievalProviderUnavailableError
from apps.retrieval.query.policy import RerankCandidate, RerankScore


class SiliconFlowReranker:
    """调用 SiliconFlow rerank API，对现有候选重新评分。"""

    provider = "siliconflow"

    def __init__(
        self,
        *,
        api_base_url: str,
        api_key: str,
        model: str,
        timeout: float,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def rerank(
        self,
        query: str,
        candidates: tuple[RerankCandidate, ...],
    ) -> list[RerankScore]:
        if not candidates:
            return []
        documents = [
            "\n".join(dict.fromkeys(part for part in (item.title, item.snippet) if part))
            for item in candidates
        ]
        try:
            response = httpx.post(
                f"{self.api_base_url}/rerank",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "return_documents": False,
                    "top_n": len(documents),
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload: Any = response.json()
        except httpx.TimeoutException as exc:
            raise RetrievalProviderUnavailableError(
                "检索 reranker 请求超时",
                details={"reason_code": "RERANKER_TIMEOUT"},
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RetrievalProviderUnavailableError(
                "检索 reranker 请求失败",
                details={"reason_code": "RERANKER_REQUEST_FAILED"},
            ) from exc

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            self._raise_invalid_response()
        scores: list[RerankScore] = []
        seen_indices: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                self._raise_invalid_response()
            index = item.get("index")
            score = item.get("relevance_score")
            if (
                not isinstance(index, int)
                or isinstance(score, bool)
                or not isinstance(score, int | float)
                or index < 0
                or index >= len(candidates)
                or index in seen_indices
            ):
                self._raise_invalid_response()
            seen_indices.add(index)
            scores.append(
                RerankScore(
                    candidate_id=candidates[index].candidate_id,
                    score=float(score),
                )
            )
        if len(scores) != len(candidates):
            self._raise_invalid_response()
        return scores

    @staticmethod
    def _raise_invalid_response() -> None:
        raise RetrievalProviderUnavailableError(
            "检索 reranker 返回结构无效",
            details={"reason_code": "RERANKER_RESPONSE_INVALID"},
        )


__all__ = ["SiliconFlowReranker"]
