"""问题分类和问题重写的确定性输入输出投影服务。"""

from __future__ import annotations

from typing import Any

from apps.chatbi.models.dto.question_understanding import (
    QuestionClassificationOutputBase,
    QuestionRewriteProjectionOutput,
)


class QuestionInputProjectionService:
    """统一 Graph 进入意图识别前的确定性投影规则。"""

    @staticmethod
    def classification_precondition(
        question: str,
        dataset_id: int | None,
    ) -> dict[str, Any] | None:
        """返回无需调用模型的分类结果；可继续分类时返回空值。"""

        if not question:
            return QuestionClassificationOutputBase(
                category="forbidden",
                reason="empty_question",
                risk_level="medium",
                confidence=1.0,
            ).model_dump(mode="json")
        if dataset_id is None:
            return QuestionClassificationOutputBase(
                category="forbidden",
                reason="missing_dataset",
                risk_level="medium",
                confidence=1.0,
            ).model_dump(mode="json")
        return None

    @staticmethod
    def project_classification(payload: dict[str, Any]) -> dict[str, Any]:
        """校验并输出稳定的问题分类结构。"""

        return QuestionClassificationOutputBase.model_validate(payload).model_dump(
            mode="json"
        )

    @staticmethod
    def project_rewrite(
        payload: dict[str, Any],
        dataset_id: int | None,
    ) -> dict[str, Any]:
        """校验重写输出，并清除模型误报的已提供数据集槽位。"""

        output = QuestionRewriteProjectionOutput.model_validate(payload)
        if dataset_id is None or "dataset_id" not in output.missing_slots:
            return output.model_dump(mode="json")
        missing_slots = [
            slot for slot in output.missing_slots if slot != "dataset_id"
        ]
        return output.model_copy(
            update={
                "missing_slots": missing_slots,
                "need_user_input": bool(missing_slots),
            }
        ).model_dump(mode="json")

    @staticmethod
    def empty_rewrite() -> dict[str, Any]:
        """投影空问题的固定澄清结果。"""

        return QuestionRewriteProjectionOutput(
            rewritten_question="",
            need_user_input=True,
            missing_slots=["question"],
            image_profile_hint=None,
        ).model_dump(mode="json")

    @staticmethod
    def fallback_rewrite(
        question: str,
        user_feedback: dict[str, Any],
    ) -> dict[str, Any]:
        """模型失败时生成 Graph 既有的最小重写降级结果。"""

        need_user_input = not user_feedback and any(
            keyword in question for keyword in ("需要澄清", "信息不足", "补充")
        )
        return QuestionRewriteProjectionOutput(
            rewritten_question=question,
            need_user_input=need_user_input,
            missing_slots=["metric"] if need_user_input else [],
            image_profile_hint=None,
        ).model_dump(mode="json")


__all__ = ["QuestionInputProjectionService"]
