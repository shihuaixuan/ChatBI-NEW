import pytest

from apps.capabilities import question_understanding as legacy_question_understanding
from apps.chatbi.services.question_understanding_service import (
    QuestionUnderstandingError,
    QuestionUnderstandingService,
    apply_question_understanding_clarification,
)


def test_question_understanding_service_requires_explicit_model_boundary():
    with pytest.raises(
        ValueError,
        match="QUESTION_UNDERSTANDING_MODEL_SERVICE_REQUIRED",
    ):
        QuestionUnderstandingService()


def test_legacy_question_understanding_path_reexports_chatbi_objects():
    assert legacy_question_understanding.QuestionUnderstandingService is QuestionUnderstandingService
    assert legacy_question_understanding.QuestionUnderstandingError is QuestionUnderstandingError
    assert (
        legacy_question_understanding.apply_question_understanding_clarification
        is apply_question_understanding_clarification
    )
