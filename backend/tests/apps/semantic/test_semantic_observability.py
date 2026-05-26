from apps.semantic.services.semantic_observability import (
    SEMANTIC_METRIC_NAMES,
    get_semantic_metric_count,
    record_semantic_metric,
    reset_semantic_metric_counts,
)


def test_semantic_metric_names_are_reserved():
    assert SEMANTIC_METRIC_NAMES == {
        "semantic_candidate_init_total",
        "semantic_asset_approved_total",
        "semantic_search_latency_ms",
        "semantic_search_empty_total",
        "semantic_embedding_failed_total",
        "semantic_expr_validate_failed_total",
        "semantic_prompt_injected_total",
    }


def test_record_semantic_metric_increments_and_sanitizes_logs(monkeypatch):
    logged = []
    monkeypatch.setattr(
        "apps.semantic.services.semantic_observability.SQLBotLogUtil.info",
        lambda message: logged.append(message),
    )
    reset_semantic_metric_counts()

    record_semantic_metric(
        "semantic_expr_validate_failed_total",
        asset_id=7,
        expr="amount password=secret postgresql://user:secret@localhost/db token=abc",
    )

    assert get_semantic_metric_count("semantic_expr_validate_failed_total") == 1
    assert "asset_id=7" in logged[0]
    assert "secret" not in logged[0]
    assert "postgresql://user" not in logged[0]
    assert "password=[REDACTED]" in logged[0]
    assert "token=[REDACTED]" in logged[0]
