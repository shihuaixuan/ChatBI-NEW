from apps.semantic.models.orm import SemanticMetric, SemanticModel
from apps.semantic.repository.sqlmodel.metric_repository import (
    SqlModelMetricRepository,
)


def test_metric_repository_syncs_relations_and_schema_version_before_commit(
    monkeypatch,
):
    session = _AssetSession()
    model = SemanticModel(
        id=9,
        oid=1,
        domain_id=2,
        datasource_id=7,
        name="订单模型",
        biz_name="orders",
    )
    metric = SemanticMetric(
        oid=1,
        model_id=9,
        name="销售额",
        biz_name="sales_amount",
    )
    monkeypatch.setattr(
        "apps.semantic.repository.sqlmodel.metric_repository.sync_metric_relations",
        lambda current_session, current_metric: current_session.events.append(
            ("sync", current_metric)
        ),
    )
    monkeypatch.setattr(
        "apps.semantic.repository.sqlmodel.metric_repository.mark_model_schema_changed",
        lambda current_session, current_model: current_session.events.append(
            ("version", current_model)
        ),
    )

    result = SqlModelMetricRepository(session).create(metric, model)

    assert result is metric
    assert session.events == [
        ("add", metric),
        ("flush", None),
        ("refresh", metric),
        ("sync", metric),
        ("version", model),
        ("commit", None),
        ("refresh", metric),
    ]


class _AssetSession:
    def __init__(self):
        self.events: list[tuple[str, object | None]] = []

    def add(self, entity):
        self.events.append(("add", entity))

    def flush(self):
        self.events.append(("flush", None))

    def refresh(self, entity):
        self.events.append(("refresh", entity))

    def commit(self):
        self.events.append(("commit", None))
