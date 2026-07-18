from apps.semantic.models.orm import SemanticDimension, SemanticModel
from apps.semantic.repository.model_repository import SemanticModelAssetBundle
from apps.semantic.repository.sqlmodel.model_repository import (
    SqlModelModelRepository,
)


def test_model_repository_creates_model_and_assets_in_one_transaction(monkeypatch):
    session = _ModelSession()
    model = SemanticModel(
        oid=1,
        domain_id=2,
        datasource_id=7,
        name="订单模型",
        biz_name="orders",
    )
    dimension = SemanticDimension(
        oid=1,
        model_id=0,
        name="订单日期",
        biz_name="order_date",
    )
    bundle = SemanticModelAssetBundle(
        model=model,
        dimensions=[dimension],
        metrics=[],
    )
    monkeypatch.setattr(
        "apps.semantic.repository.sqlmodel.model_repository.sync_model_structure",
        lambda current_session, current_model: current_session.events.append(
            ("sync", current_model)
        ),
    )

    result = SqlModelModelRepository(session).create_with_assets(bundle)

    assert result is bundle
    assert model.id == 30
    assert dimension.model_id == 30
    assert session.events == [
        ("add", model),
        ("flush", None),
        ("refresh", model),
        ("add", dimension),
        ("sync", model),
        ("commit", None),
        ("refresh", model),
    ]


class _ModelSession:
    def __init__(self):
        self.events: list[tuple[str, object | None]] = []

    def add(self, entity):
        self.events.append(("add", entity))

    def flush(self):
        self.events.append(("flush", None))

    def refresh(self, entity):
        if isinstance(entity, SemanticModel) and entity.id is None:
            entity.id = 30
        self.events.append(("refresh", entity))

    def commit(self):
        self.events.append(("commit", None))
