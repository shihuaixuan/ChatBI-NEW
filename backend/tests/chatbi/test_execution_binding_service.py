import pytest

from apps.chatbi.models import ExecutionBindingData
from apps.chatbi.services import ExecutionBindingError, ExecutionBindingService


def test_execution_binding_keeps_conversation_dataset_and_datasource():
    binding = ExecutionBindingService().resolve(
        ExecutionBindingData(
            conversation_dataset_id=20,
            conversation_datasource_id=30,
            requested_dataset_id=20,
            requested_datasource_id=30,
            require_dataset=True,
        )
    )

    assert binding.dataset_id == 20
    assert binding.datasource_id == 30


@pytest.mark.parametrize(
    ("data", "error_code"),
    [
        (
            ExecutionBindingData(
                conversation_dataset_id=20,
                conversation_datasource_id=30,
                requested_dataset_id=21,
            ),
            "CHAT_DATASET_MISMATCH",
        ),
        (
            ExecutionBindingData(
                conversation_dataset_id=20,
                conversation_datasource_id=30,
                requested_datasource_id=31,
            ),
            "CHAT_DATASOURCE_MISMATCH",
        ),
        (
            ExecutionBindingData(
                conversation_dataset_id=None,
                conversation_datasource_id=30,
                require_dataset=True,
            ),
            "CHAT_DATASET_REQUIRED",
        ),
        (
            ExecutionBindingData(
                conversation_dataset_id=None,
                conversation_datasource_id=None,
            ),
            "CHAT_DATASOURCE_REQUIRED",
        ),
    ],
)
def test_execution_binding_rejects_inconsistent_context(data, error_code):
    with pytest.raises(ExecutionBindingError, match=error_code):
        ExecutionBindingService().resolve(data)


def test_unbound_conversation_can_use_explicit_datasource_without_fake_dataset():
    binding = ExecutionBindingService().resolve(
        ExecutionBindingData(
            conversation_dataset_id=None,
            conversation_datasource_id=None,
            requested_datasource_id=30,
        )
    )

    assert binding.dataset_id is None
    assert binding.datasource_id == 30
