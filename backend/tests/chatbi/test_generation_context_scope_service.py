from apps.chatbi.models import (
    GenerationAssistantContext,
    GenerationContextScopeData,
)
from apps.chatbi.services import resolve_generation_scope


def test_scope_without_assistant_keeps_requested_workspace_and_datasource():
    result = resolve_generation_scope(
        GenerationContextScopeData(
            default_workspace_id=10,
            current_user_workspace_id=20,
            datasource_id=30,
        )
    )

    assert result.workspace_id == 10
    assert result.datasource_id == 30
    assert result.sql_example_assistant_id is None
    assert result.use_assistant_sql_examples is False


def test_regular_assistant_uses_assistant_workspace_and_datasource_scope():
    result = resolve_generation_scope(
        GenerationContextScopeData(
            default_workspace_id=10,
            current_user_workspace_id=20,
            datasource_id=30,
            assistant=GenerationAssistantContext(
                assistant_id=40,
                workspace_id=50,
                assistant_type=0,
            ),
        )
    )

    assert result.workspace_id == 50
    assert result.datasource_id == 30
    assert result.sql_example_assistant_id is None
    assert result.use_assistant_sql_examples is False


def test_advanced_assistant_uses_assistant_sql_examples_without_datasource():
    result = resolve_generation_scope(
        GenerationContextScopeData(
            default_workspace_id=10,
            current_user_workspace_id=20,
            datasource_id=30,
            assistant=GenerationAssistantContext(
                assistant_id=None,
                workspace_id=50,
                assistant_type=1,
            ),
        )
    )

    assert result.workspace_id == 50
    assert result.datasource_id is None
    assert result.sql_example_assistant_id is None
    assert result.use_assistant_sql_examples is True


def test_page_embedded_assistant_uses_current_user_workspace():
    result = resolve_generation_scope(
        GenerationContextScopeData(
            default_workspace_id=10,
            current_user_workspace_id=20,
            datasource_id=30,
            assistant=GenerationAssistantContext(
                assistant_id=40,
                workspace_id=50,
                assistant_type=4,
            ),
        )
    )

    assert result.workspace_id == 20
    assert result.datasource_id == 30
    assert result.use_assistant_sql_examples is False
