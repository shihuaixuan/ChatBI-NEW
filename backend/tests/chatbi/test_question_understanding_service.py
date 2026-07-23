import pytest

from apps.chatbi.services.understanding.understanding_service import (
    QuestionUnderstandingService,
)


def test_question_understanding_service_requires_explicit_model_boundary():
    with pytest.raises(
        ValueError,
        match="QUESTION_UNDERSTANDING_MODEL_SERVICE_REQUIRED",
    ):
        QuestionUnderstandingService()
