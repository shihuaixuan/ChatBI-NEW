"""派生指标和比率指标的受控表达式展开。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any


class MetricExpansionError(ValueError):
    """指标引用无法形成安全表达式。"""


def build_ratio_expression(numerator: str, denominator: str) -> str:
    """生成带除零保护的比率表达式。"""

    if not numerator.strip() or not denominator.strip():
        raise MetricExpansionError("METRIC_RATIO_OPERAND_REQUIRED")
    return f"({numerator}) / NULLIF(({denominator}), 0)"


def expand_metric_expression(
    metric: Mapping[str, Any],
    metrics_by_id: Mapping[int, Mapping[str, Any]],
    renderer: Callable[[Mapping[str, Any]], str],
    *,
    stack: tuple[int, ...] = (),
) -> str:
    """展开 metric_refs，表达式中的引用只允许来自已绑定指标。"""

    metric_id = _positive_int(metric.get("id"))
    if metric_id is not None and metric_id in stack:
        raise MetricExpansionError("METRIC_REFERENCE_CYCLE")
    refs = _metric_refs(metric)
    if not refs:
        return renderer(metric)
    missing = [ref for ref in refs if ref not in metrics_by_id]
    if missing:
        raise MetricExpansionError("METRIC_REFERENCE_NOT_FOUND")
    next_stack = (*stack, metric_id) if metric_id is not None else stack
    rendered = {
        ref: expand_metric_expression(
            metrics_by_id[ref],
            metrics_by_id,
            renderer,
            stack=next_stack,
        )
        for ref in refs
    }
    formula = _metric_formula_definition(metric)
    if isinstance(formula, Mapping):
        return _render_structured_formula(formula, rendered)
    raw_expression = _metric_expression(metric)
    if not raw_expression:
        if len(refs) != 2:
            raise MetricExpansionError("METRIC_DERIVED_EXPRESSION_REQUIRED")
        return build_ratio_expression(rendered[refs[0]], rendered[refs[1]])
    expanded = _substitute_references(raw_expression, metric, metrics_by_id, rendered)
    if len(refs) == 2 and "/" in raw_expression and "nullif" not in raw_expression.lower():
        simple_ratio = re.fullmatch(r"\s*[A-Za-z_][A-Za-z0-9_]*\s*/\s*[A-Za-z_][A-Za-z0-9_]*\s*", raw_expression)
        if simple_ratio:
            return build_ratio_expression(rendered[refs[0]], rendered[refs[1]])
    return expanded


def _metric_refs(metric: Mapping[str, Any]) -> tuple[int, ...]:
    formula = _metric_formula_definition(metric)
    refs = metric.get("metric_refs")
    if isinstance(formula, Mapping):
        refs = [
            item.get("metric_id")
            for item in formula.get("components") or []
            if isinstance(item, Mapping)
        ]
    if not isinstance(refs, list):
        params = metric.get("type_params")
        params = params if isinstance(params, Mapping) else {}
        metric_params = params.get("metricDefineByMetricParams")
        metric_params = metric_params if isinstance(metric_params, Mapping) else {}
        refs = [
            item.get("id")
            for item in metric_params.get("metrics") or []
            if isinstance(item, Mapping)
        ]
    result = tuple(item for item in (_positive_int(value) for value in refs) if item is not None)
    if len(result) != len(set(result)):
        raise MetricExpansionError("METRIC_REFERENCE_DUPLICATED")
    return result


def _metric_formula_definition(metric: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """统一读取运行时 Schema 中的结构化公式。"""

    formula = metric.get("formula_definition")
    if isinstance(formula, Mapping):
        return formula
    ext_info = metric.get("ext_info")
    if isinstance(ext_info, Mapping) and isinstance(
        ext_info.get("formula_definition"), Mapping
    ):
        return ext_info["formula_definition"]
    return None


def _render_structured_formula(
    formula: Mapping[str, Any],
    rendered: Mapping[int, str],
) -> str:
    """将已校验的结构化公式编译为受控表达式。"""

    operation = formula.get("operation")
    components = formula.get("components") or []
    expressions = [
        rendered[item["metric_id"]]
        for item in components
        if isinstance(item, Mapping) and item.get("metric_id") in rendered
    ]
    if len(expressions) != len(components):
        raise MetricExpansionError("METRIC_REFERENCE_NOT_FOUND")
    if operation == "RATIO":
        by_role = {
            item["role"]: rendered[item["metric_id"]]
            for item in components
            if isinstance(item, Mapping)
        }
        return build_ratio_expression(by_role["numerator"], by_role["denominator"])
    if operation == "SUM":
        return " + ".join(f"({item})" for item in expressions)
    if operation == "DIFFERENCE":
        by_role = {
            item["role"]: rendered[item["metric_id"]]
            for item in components
            if isinstance(item, Mapping)
        }
        return f"({by_role['minuend']}) - ({by_role['subtrahend']})"
    if operation == "PRODUCT":
        return " * ".join(f"({item})" for item in expressions)
    raise MetricExpansionError("METRIC_FORMULA_INVALID")


def _metric_expression(metric: Mapping[str, Any]) -> str:
    params = metric.get("type_params")
    params = params if isinstance(params, Mapping) else {}
    metric_params = params.get("metricDefineByMetricParams")
    metric_params = metric_params if isinstance(metric_params, Mapping) else {}
    return str(metric_params.get("expr") or metric.get("expr") or "").strip()


def _substitute_references(
    expression: str,
    metric: Mapping[str, Any],
    metrics_by_id: Mapping[int, Mapping[str, Any]],
    rendered: Mapping[int, str],
) -> str:
    """只替换指标业务名或 metric_N 占位符，禁止替换未知标识符。"""

    names: dict[str, str] = {}
    for ref, value in rendered.items():
        target = metrics_by_id[ref]
        for key in (target.get("biz_name"), target.get("name"), f"metric_{ref}"):
            if isinstance(key, str) and key.strip():
                names[key.strip()] = value
    if not names:
        raise MetricExpansionError("METRIC_REFERENCE_NOT_FOUND")
    result = expression
    for name in sorted(names, key=len, reverse=True):
        result = re.sub(rf"\b{re.escape(name)}\b", f"({names[name]})", result)
    unresolved = {
        token
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", result)
        if token.lower() not in {"nullif", "coalesce", "case", "when", "then", "else", "end"}
    }
    # 业务指标表达式允许函数和数值常量，但不能静默保留未绑定指标名。
    original_names = set(names)
    declared = {
        str(metric.get("biz_name") or ""),
        str(metric.get("name") or ""),
    }
    functions = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", result))
    unknown = unresolved - original_names - declared - functions
    if unknown and any(character.isalpha() for character in "".join(unknown)):
        raise MetricExpansionError("METRIC_DERIVED_EXPRESSION_UNKNOWN_REFERENCE")
    return result


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


__all__ = [
    "MetricExpansionError",
    "build_ratio_expression",
    "expand_metric_expression",
]
