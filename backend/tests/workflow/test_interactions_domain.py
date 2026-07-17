from datetime import datetime, timezone

from apps.workflow.capabilities.interactions import (
    build_interaction_record,
    legacy_interaction_path,
    read_interaction_response,
    standard_interaction_path,
)


def test_build_interaction_record_normalizes_skipped_and_timestamp():
    answered_at = datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)

    record = build_interaction_record(
        "ask_slot_clarification",
        {"skipped": True},
        round_count=2,
        answered_at=answered_at,
    )

    assert record == {
        "node_name": "ask_slot_clarification",
        "round": 2,
        "response": {"skipped": True},
        "skipped": True,
        "answered_at": "2026-07-07T10:00:00+00:00",
    }


def test_interaction_paths_are_stable_for_metric_selection():
    assert standard_interaction_path("ask_metric_selection") == "variables.interactions.ask_metric_selection"
    assert legacy_interaction_path("ask_metric_selection") == "variables.metric_selection"


def test_read_interaction_response_falls_back_to_legacy_key():
    variables = {"metric_selection": {"metric": 239}}

    response = read_interaction_response(
        variables,
        "ask_metric_selection",
        legacy_key="metric_selection",
    )

    assert response == {"metric": 239}
