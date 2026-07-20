from typing import Protocol

from apps.assistant import AssistantHeader
from apps.assistant.services import (
    EXTERNAL_DATASOURCE_ASSISTANT_TYPES,
    PAGE_EMBEDDED_ASSISTANT_TYPE,
    AssistantService,
)
from apps.chatbi.models import (
    DatasourceSelectionCandidate,
    DatasourceSelectionRankingCandidate,
)
from apps.chatbi.services.datasource_selection_service import (
    DatasourceSelectionError,
)
from apps.datasource import ExternalDatasource
from apps.datasource.services import DatasourceService


class DatasourceSelectionCandidateRanker(Protocol):
    """本地数据源候选相关性排序端口。"""

    def rank(
        self,
        question: str,
        candidates: list[DatasourceSelectionRankingCandidate],
        *,
        limit: int,
    ) -> list[int]: ...


class DatasourceSelectionCandidateService:
    """读取数据源选择范围，并对本地候选进行相关性排序。"""

    def __init__(
        self,
        assistant_service: AssistantService,
        datasource_service: DatasourceService,
        ranker: DatasourceSelectionCandidateRanker,
        *,
        embedding_enabled: bool,
        embedding_limit: int,
    ) -> None:
        if embedding_limit <= 0:
            raise ValueError("DATASOURCE_SELECTION_EMBEDDING_LIMIT_INVALID")
        self._assistant_service = assistant_service
        self._datasource_service = datasource_service
        self._ranker = ranker
        self._embedding_enabled = embedding_enabled
        self._embedding_limit = embedding_limit

    def list_candidates(
        self,
        workspace_id: int,
        assistant: AssistantHeader | None,
        question: str,
        *,
        embedding: bool = True,
        external_datasources: list[ExternalDatasource] | None = None,
    ) -> list[DatasourceSelectionCandidate]:
        is_external = (
            assistant is not None
            and assistant.type in EXTERNAL_DATASOURCE_ASSISTANT_TYPES
        )
        local_records = (
            []
            if is_external
            else self._datasource_service.list_by_workspace(workspace_id)
        )

        if assistant is not None and assistant.type != PAGE_EMBEDDED_ASSISTANT_TYPE:
            summaries = self._assistant_service.list_datasources(
                assistant,
                external_datasources=external_datasources,
            )
            candidates: list[DatasourceSelectionCandidate] = []
            for summary in summaries:
                if isinstance(summary.id, bool):
                    raise DatasourceSelectionError(
                        "DATASOURCE_SELECTION_CANDIDATE_ID_INVALID"
                    )
                try:
                    datasource_id = int(summary.id)
                except (TypeError, ValueError) as exc:
                    raise DatasourceSelectionError(
                        "DATASOURCE_SELECTION_CANDIDATE_ID_INVALID"
                    ) from exc
                if datasource_id <= 0 or not summary.name.strip():
                    raise DatasourceSelectionError(
                        "DATASOURCE_SELECTION_CANDIDATE_INVALID"
                    )
                candidates.append(
                    DatasourceSelectionCandidate(
                        id=datasource_id,
                        name=summary.name,
                        description=summary.description,
                    )
                )
        else:
            candidates = []
            for record in local_records:
                if record.id is None or record.id <= 0 or not record.name.strip():
                    raise DatasourceSelectionError(
                        "DATASOURCE_SELECTION_CANDIDATE_INVALID"
                    )
                candidates.append(
                    DatasourceSelectionCandidate(
                        id=record.id,
                        name=record.name,
                        description=record.description,
                    )
                )

        records_by_id = {
            record.id: record
            for record in local_records
            if record.id is not None
        }
        if not is_external:
            missing_ids = [
                candidate.id
                for candidate in candidates
                if candidate.id not in records_by_id
            ]
            if missing_ids:
                raise DatasourceSelectionError(
                    "DATASOURCE_SELECTION_LOCAL_CANDIDATE_NOT_FOUND:"
                    f"{missing_ids[0]}"
                )

        if (
            len(candidates) <= 1
            or is_external
            or not embedding
            or not self._embedding_enabled
        ):
            return candidates

        ranking_candidates: list[DatasourceSelectionRankingCandidate] = []
        for candidate in candidates:
            ranking_record = records_by_id.get(candidate.id)
            if ranking_record is None:
                raise DatasourceSelectionError(
                    f"DATASOURCE_SELECTION_LOCAL_CANDIDATE_NOT_FOUND:{candidate.id}"
                )
            ranking_candidates.append(
                DatasourceSelectionRankingCandidate(
                    id=candidate.id,
                    embedding=ranking_record.embedding,
                )
            )

        ranked_ids = self._ranker.rank(
            question,
            ranking_candidates,
            limit=self._embedding_limit,
        )
        candidates_by_id = {candidate.id: candidate for candidate in candidates}
        if len(ranked_ids) != len(set(ranked_ids)) or any(
            datasource_id not in candidates_by_id for datasource_id in ranked_ids
        ):
            raise DatasourceSelectionError(
                "DATASOURCE_SELECTION_RANKING_RESULT_INVALID"
            )
        return [candidates_by_id[datasource_id] for datasource_id in ranked_ids]


__all__ = [
    "DatasourceSelectionCandidateRanker",
    "DatasourceSelectionCandidateService",
]
