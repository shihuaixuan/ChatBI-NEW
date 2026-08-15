"""用户记忆离线评测服务。"""

from apps.memory.errors import MemoryValidationError
from apps.memory.models.dto import (
    MemoryEvaluationCaseResult,
    MemoryEvaluationResult,
    MemoryEvaluationSample,
)


class MemoryEvaluationService:
    """根据标注样本计算用户记忆召回和采用指标。"""

    def evaluate(
        self,
        samples: list[MemoryEvaluationSample],
    ) -> MemoryEvaluationResult:
        if not samples:
            raise MemoryValidationError("MEMORY_EVALUATION_SAMPLES_EMPTY")

        cases: list[MemoryEvaluationCaseResult] = []
        totals = {
            "expected_count": 0,
            "retrieved_count": 0,
            "relevant_count": 0,
            "false_positive_count": 0,
            "adopted_count": 0,
            "evaluated_count": 0,
        }
        for sample in samples:
            expected = self._memory_ids(sample.case_id, "expected", sample.expected_memory_ids)
            retrieved = self._memory_ids(sample.case_id, "retrieved", sample.retrieved_memory_ids)
            adopted = self._memory_ids(sample.case_id, "adopted", sample.adopted_memory_ids)
            rejected = self._memory_ids(sample.case_id, "rejected", sample.rejected_memory_ids)
            if adopted & rejected:
                raise MemoryValidationError(
                    f"MEMORY_EVALUATION_ADOPTION_CONFLICT:{sample.case_id}"
                )
            if not (adopted | rejected) <= retrieved:
                raise MemoryValidationError(
                    f"MEMORY_EVALUATION_LABEL_NOT_RETRIEVED:{sample.case_id}"
                )

            relevant_count = len(expected & retrieved)
            false_positive_count = len(retrieved - expected)
            evaluated_count = len(adopted | rejected)
            case = MemoryEvaluationCaseResult(
                case_id=sample.case_id,
                expected_count=len(expected),
                retrieved_count=len(retrieved),
                relevant_count=relevant_count,
                false_positive_count=false_positive_count,
                adopted_count=len(adopted),
                evaluated_count=evaluated_count,
                recall=self._rate(relevant_count, len(expected)),
                precision=self._rate(relevant_count, len(retrieved)),
                f1=self._f1(relevant_count, len(expected), len(retrieved)),
                wrong_memory_rate=self._rate(false_positive_count, len(retrieved)),
                adoption_rate=self._rate(len(adopted), evaluated_count),
            )
            cases.append(case)
            for key in totals:
                totals[key] += getattr(case, key)

        return MemoryEvaluationResult(
            sample_count=len(cases),
            **totals,
            recall=self._rate(totals["relevant_count"], totals["expected_count"]),
            precision=self._rate(
                totals["relevant_count"], totals["retrieved_count"]
            ),
            f1=self._f1(
                totals["relevant_count"],
                totals["expected_count"],
                totals["retrieved_count"],
            ),
            wrong_memory_rate=self._rate(
                totals["false_positive_count"], totals["retrieved_count"]
            ),
            adoption_rate=self._rate(
                totals["adopted_count"], totals["evaluated_count"]
            ),
            cases=cases,
        )

    @staticmethod
    def _memory_ids(case_id: str, label: str, values: list[int]) -> set[int]:
        if any(value <= 0 for value in values) or len(set(values)) != len(values):
            raise MemoryValidationError(
                f"MEMORY_EVALUATION_IDS_INVALID:{case_id}:{label}"
            )
        return set(values)

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    @staticmethod
    def _f1(relevant: int, expected: int, retrieved: int) -> float | None:
        return 2 * relevant / (expected + retrieved) if expected + retrieved else None


__all__ = ["MemoryEvaluationService"]
