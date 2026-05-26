from apps.semantic.models.semantic_model import AssetType
from apps.semantic.models.semantic_schema import (
    SemanticAssetMatch,
    SemanticRetrieveResponse,
)
from apps.semantic.services.semantic_context import build_prompt_context


def test_build_prompt_context_renders_metrics_dimensions_and_truncates_description():
    long_description = "指标口径" * 80
    response = SemanticRetrieveResponse(
        metrics=[
            SemanticAssetMatch(
                asset_type=AssetType.METRIC.value,
                asset_id=1,
                name="sales_amount",
                display_name="销售额",
                score=0.91,
                snapshot={
                    "aliases": ["GMV", "收入"],
                    "description": long_description,
                    "expr": "pay_amount",
                    "default_agg": "SUM",
                },
            )
        ],
        dimensions=[
            SemanticAssetMatch(
                asset_type=AssetType.DIMENSION.value,
                asset_id=2,
                name="order_date",
                display_name="下单日期",
                score=0.88,
                snapshot={
                    "aliases": ["日期"],
                    "description": "订单创建日期",
                    "expr": "order_date",
                    "dimension_type": "TIME",
                    "semantic_type": "DATE",
                    "time_granularities": ["day", "month"],
                },
            )
        ],
    )

    context = build_prompt_context(response)

    assert "已审核业务指标" in context
    assert "销售额" in context
    assert "sales_amount" in context
    assert "默认聚合: SUM" in context
    assert "表达式: pay_amount" in context
    assert "别名: GMV, 收入" in context
    assert long_description not in context
    assert "已审核业务维度" in context
    assert "下单日期" in context
    assert "order_date" in context
    assert "维度类型: TIME" in context
    assert "语义类型: DATE" in context
    assert "时间粒度: day, month" in context


def test_build_prompt_context_filters_low_score_limits_length_and_returns_empty_for_no_matches():
    low_score = SemanticAssetMatch(
        asset_type=AssetType.METRIC.value,
        asset_id=1,
        name="low_score_metric",
        display_name="低分指标",
        score=0.54,
        snapshot={"expr": "x", "default_agg": "SUM"},
    )
    high_score_matches = [
        SemanticAssetMatch(
            asset_type=AssetType.METRIC.value,
            asset_id=index,
            name=f"metric_{index}",
            display_name=f"指标{index}",
            score=0.9,
            snapshot={"description": "长描述" * 200, "expr": "amount", "default_agg": "SUM"},
        )
        for index in range(80)
    ]

    assert build_prompt_context(SemanticRetrieveResponse()) == ""

    context = build_prompt_context(SemanticRetrieveResponse(metrics=[low_score, *high_score_matches]))

    assert "低分指标" not in context
    assert len(context) <= 3000
