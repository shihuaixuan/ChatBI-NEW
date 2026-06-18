from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from apps.data_training.models.data_training_model import DataTraining
from apps.semantic.assets.enums import AssetType
from apps.semantic.models.semantic_model import (
    DimensionType,
    SemanticDimension,
    SemanticDimensionValue,
    SemanticMetric,
    SemanticType,
)
from apps.terminology.models.terminology_model import Terminology


class AssetQualityIssue(BaseModel):
    level: str
    code: str
    message: str
    suggestion: str | None = None


class AssetQualityResult(BaseModel):
    asset_type: AssetType
    asset_id: int | str | None = None
    score: float = 1.0
    issues: list[AssetQualityIssue] = Field(default_factory=list)


class AssetQualityService:
    def check_metric(self, metric: SemanticMetric) -> AssetQualityResult:
        issues: list[AssetQualityIssue] = []
        if not metric.display_name:
            issues.append(_issue("error", "metric.display_name_missing", "指标缺少展示名", "补充业务可理解的指标展示名"))
        if not metric.description:
            issues.append(_issue("warning", "metric.description_missing", "指标缺少口径说明", "补充计算口径、适用场景和相似指标差异"))
        if not metric.expr:
            issues.append(_issue("error", "metric.expression_missing", "指标缺少表达式或字段来源", "补充指标表达式或来源字段"))
        if not metric.default_agg:
            issues.append(_issue("warning", "metric.default_agg_missing", "指标缺少默认聚合方式", "补充 SUM、COUNT、COUNT_DISTINCT 等默认聚合"))
        if len(metric.display_name or "") <= 2 and not metric.aliases:
            issues.append(_issue("warning", "metric.name_too_broad", "指标名称过宽且缺少别名说明", "补充更明确的名称或差异说明"))
        return AssetQualityResult(
            asset_type=AssetType.METRIC,
            asset_id=metric.id,
            score=_score(issues),
            issues=issues,
        )

    def check_dimension(self, dimension: SemanticDimension) -> AssetQualityResult:
        issues: list[AssetQualityIssue] = []
        if not dimension.display_name:
            issues.append(_issue("error", "dimension.display_name_missing", "维度缺少展示名", "补充业务可理解的维度展示名"))
        if not dimension.expr:
            issues.append(_issue("error", "dimension.expression_missing", "维度缺少表达式或字段来源", "补充维度表达式或来源字段"))
        if not dimension.dimension_type:
            issues.append(_issue("warning", "dimension.type_missing", "维度缺少维度类型", "补充 CATEGORY、TIME、GEO 或 ID"))
        if not dimension.semantic_type or dimension.semantic_type == SemanticType.UNKNOWN.value:
            issues.append(_issue("warning", "dimension.semantic_type_unknown", "维度语义类型未知", "补充 DATE、REGION、CHANNEL、USER 等语义类型"))
        if dimension.dimension_type == DimensionType.TIME.value and not dimension.time_granularities:
            issues.append(_issue("warning", "dimension.time_granularity_missing", "时间维度缺少时间粒度", "补充可支持的 day、week、month 等粒度"))
        return AssetQualityResult(
            asset_type=AssetType.DIMENSION,
            asset_id=dimension.id,
            score=_score(issues),
            issues=issues,
        )

    def check_dimension_value(self, value: SemanticDimensionValue) -> AssetQualityResult:
        issues: list[AssetQualityIssue] = []
        if not value.dimension_id:
            issues.append(_issue("error", "dimension_value.dimension_missing", "维度值缺少归属维度", "补充 dimension_id"))
        if not value.value:
            issues.append(_issue("error", "dimension_value.value_missing", "维度值缺少真实查询值", "补充数据库中真实可查询的 value"))
        if not value.display_value:
            issues.append(_issue("warning", "dimension_value.display_value_missing", "维度值缺少展示值", "补充业务可理解的 display_value"))
        return AssetQualityResult(
            asset_type=AssetType.DIMENSION_VALUE,
            asset_id=value.id,
            score=_score(issues),
            issues=issues,
        )

    def check_term(self, term: Terminology) -> AssetQualityResult:
        issues: list[AssetQualityIssue] = []
        if not term.word:
            issues.append(_issue("error", "term.word_missing", "术语缺少名称", "补充业务术语名称"))
        if not term.description:
            issues.append(_issue("warning", "term.description_missing", "术语缺少解释", "补充术语含义和适用边界"))
        if not term.dataset_ids and not term.datasource_ids:
            issues.append(_issue("warning", "term.scope_missing", "术语缺少适用范围", "补充 dataset_ids 或 datasource_ids"))
        if not term.mapped_assets:
            issues.append(_issue("warning", "term.mapped_assets_missing", "术语缺少映射资产", "补充术语映射的指标、维度或维度值"))
        if len(term.mapped_assets or []) == 1 and not term.description:
            issues.append(_issue("info", "term.maybe_alias", "术语可能只是单个资产别名", "如果只是别名，建议合并到指标或维度 aliases"))
        return AssetQualityResult(
            asset_type=AssetType.TERM,
            asset_id=term.id,
            score=_score(issues),
            issues=issues,
        )

    def check_example(self, example: DataTraining) -> AssetQualityResult:
        issues: list[AssetQualityIssue] = []
        if not example.question:
            issues.append(_issue("error", "example.question_missing", "样例问题为空", "补充真实用户问法"))
        if not example.example_type:
            issues.append(_issue("warning", "example.type_missing", "样例缺少类型", "补充 SQL_EXAMPLE、QUESTION_EXAMPLE、OVERVIEW_EXAMPLE 或 CLARIFICATION_EXAMPLE"))
        if not example.linked_assets:
            issues.append(_issue("warning", "example.linked_assets_missing", "样例问题缺少关联资产", "补充样例涉及的指标、维度或过滤条件"))
        if example.example_type == "SQL_EXAMPLE" and not example.sql:
            issues.append(_issue("warning", "example.sql_missing", "SQL 示例缺少 SQL", "补充可参考的 SQL"))
        if example.example_type == "SQL_EXAMPLE" and example.sql and not _looks_like_select_sql(example.sql):
            issues.append(_issue("warning", "example.sql_unparseable", "SQL 示例无法识别为查询 SQL", "补充 SELECT 或 WITH 开头的可参考 SQL"))
        if example.dataset_id is None and example.datasource is None:
            issues.append(_issue("warning", "example.scope_missing", "样例缺少数据集或数据源范围", "补充 dataset_id 或 datasource"))
        return AssetQualityResult(
            asset_type=AssetType.EXAMPLE,
            asset_id=example.id,
            score=_score(issues),
            issues=issues,
        )

    def check_dataset_assets(self, assets: list[Any]) -> list[AssetQualityResult]:
        results: list[AssetQualityResult] = []
        metric_results: dict[int | str, AssetQualityResult] = {}
        metrics: list[SemanticMetric] = []
        dimensions: list[SemanticDimension] = []
        dimension_results: dict[int | str, AssetQualityResult] = {}
        dimension_values: list[SemanticDimensionValue] = []
        dimension_value_results: dict[int | str, AssetQualityResult] = {}
        for asset in assets:
            if isinstance(asset, SemanticMetric):
                result = self.check_metric(asset)
                results.append(result)
                if asset.id is not None:
                    metric_results[asset.id] = result
                metrics.append(asset)
            elif isinstance(asset, SemanticDimension):
                result = self.check_dimension(asset)
                results.append(result)
                if asset.id is not None:
                    dimension_results[asset.id] = result
                dimensions.append(asset)
            elif isinstance(asset, SemanticDimensionValue):
                result = self.check_dimension_value(asset)
                results.append(result)
                if asset.id is not None:
                    dimension_value_results[asset.id] = result
                dimension_values.append(asset)
            elif isinstance(asset, Terminology):
                results.append(self.check_term(asset))
            elif isinstance(asset, DataTraining):
                results.append(self.check_example(asset))
        if not results:
            results.append(
                AssetQualityResult(
                    asset_type=AssetType.DATASET,
                    asset_id=None,
                    score=0.0,
                    issues=[
                        _issue("warning", "dataset.assets_empty", "数据集没有可检查资产", "补充指标、维度、术语或样例问题")
                    ],
                )
            )
        self._append_similar_metric_issues(metrics, metric_results)
        self._append_time_dimension_issues(metrics, metric_results, dimensions, dimension_results)
        self._append_dimension_value_policy_issues(dimension_values, dimension_value_results, dimensions)
        return results

    def _append_similar_metric_issues(
        self,
        metrics: list[SemanticMetric],
        metric_results: dict[int | str, AssetQualityResult],
    ) -> None:
        alias_groups: dict[str, list[SemanticMetric]] = {}
        for metric in metrics:
            for alias in metric.aliases or []:
                text = alias.strip()
                if len(text) < 2:
                    continue
                alias_groups.setdefault(text, []).append(metric)

        for alias, grouped_metrics in alias_groups.items():
            if len(grouped_metrics) < 2:
                continue
            for metric in grouped_metrics:
                if _has_difference_description(metric):
                    continue
                result = metric_results.get(metric.id)
                if result is None:
                    continue
                result.issues.append(
                    _issue(
                        "warning",
                        "metric.similar_metric_missing_difference",
                        f"指标存在相同别名“{alias}”但缺少差异说明",
                        "补充与同名或相似指标的业务边界、计算口径和使用场景差异",
                    )
                )
                result.score = _score(result.issues)

    def _append_time_dimension_issues(
        self,
        metrics: list[SemanticMetric],
        metric_results: dict[int | str, AssetQualityResult],
        dimensions: list[SemanticDimension],
        dimension_results: dict[int | str, AssetQualityResult],
    ) -> None:
        default_time_dimensions = [
            dimension
            for dimension in dimensions
            if getattr(dimension, "is_default_time", False)
        ]
        if len(default_time_dimensions) > 1:
            for dimension in default_time_dimensions:
                result = dimension_results.get(dimension.id)
                if result is None:
                    continue
                result.issues.append(
                    _issue(
                        "warning",
                        "dimension.default_time_not_unique",
                        "同一数据集存在多个默认时间维度",
                        "保留一个默认时间维度，其余时间字段作为普通时间维度使用",
                    )
                )
                result.score = _score(result.issues)

        has_default_time = bool(default_time_dimensions)
        has_time_dimension = any(dimension.dimension_type == DimensionType.TIME.value for dimension in dimensions)
        for metric in metrics:
            if metric.default_time_dimension_id or has_default_time or has_time_dimension:
                continue
            result = metric_results.get(metric.id)
            if result is None:
                continue
            result.issues.append(
                _issue(
                    "warning",
                    "metric.default_time_dimension_missing",
                    "指标缺少默认时间维度上下文",
                    "补充默认时间维度，或在数据集画像中指定可用于该指标的时间字段",
                )
            )
            result.score = _score(result.issues)

    def _append_dimension_value_policy_issues(
        self,
        values: list[SemanticDimensionValue],
        value_results: dict[int | str, AssetQualityResult],
        dimensions: list[SemanticDimension],
    ) -> None:
        dimension_by_id = {dimension.id: dimension for dimension in dimensions if dimension.id is not None}
        for value in values:
            dimension = dimension_by_id.get(value.dimension_id)
            if dimension is None:
                continue
            result = value_results.get(value.id)
            if result is None:
                continue
            if _is_high_cardinality_dimension(dimension):
                result.issues.append(
                    _issue(
                        "warning",
                        "dimension_value.high_cardinality_index_risk",
                        "高基数维度值不适合默认进入通用索引",
                        "改用回源搜索、精确过滤或仅维护高频别名实体",
                    )
                )
            if _is_sensitive_dimension(dimension):
                result.issues.append(
                    _issue(
                        "error",
                        "dimension_value.sensitive_index_risk",
                        "敏感维度值不应进入通用检索索引",
                        "从通用索引移除该维值，必要时使用受控权限的精确查询",
                    )
                )
            result.score = _score(result.issues)


def _issue(level: str, code: str, message: str, suggestion: str | None = None) -> AssetQualityIssue:
    return AssetQualityIssue(level=level, code=code, message=message, suggestion=suggestion)


def _score(issues: list[AssetQualityIssue]) -> float:
    score = 1.0
    for issue in issues:
        if issue.level == "error":
            score -= 0.3
        elif issue.level == "warning":
            score -= 0.15
    return max(score, 0.0)


def _has_difference_description(metric: SemanticMetric) -> bool:
    text = metric.description or ""
    return any(word in text for word in ("差异", "区别", "边界", "口径"))


def _looks_like_select_sql(sql: str) -> bool:
    normalized = sql.strip().lstrip("(").lower()
    return normalized.startswith("select") or normalized.startswith("with")


def _is_high_cardinality_dimension(dimension: SemanticDimension) -> bool:
    text = f"{dimension.name} {dimension.display_name} {dimension.description or ''}".lower()
    return dimension.dimension_type == DimensionType.ID.value or any(
        word in text for word in ("客户id", "用户id", "订单id", "sku", "customer_id", "user_id", "order_id")
    )


def _is_sensitive_dimension(dimension: SemanticDimension) -> bool:
    text = f"{dimension.name} {dimension.display_name} {dimension.description or ''}".lower()
    return any(word in text for word in ("phone", "mobile", "手机号", "身份证", "email", "邮箱"))
