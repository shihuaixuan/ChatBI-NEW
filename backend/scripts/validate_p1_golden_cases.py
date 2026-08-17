"""校验 P1 黄金用例 v2 的结构和稳定资产引用。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:  # 可选工具依赖缺失时必须明确报错，不能静默降级。
    raise ImportError(
        "校验黄金用例需要 jsonschema，请先安装 backend 的开发依赖。"
    ) from exc


ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "p1_golden_cases.jsonl"
SCHEMA_PATH = ROOT / "p1_golden_cases.schema.json"


def load_and_validate_cases(
    cases_path: Path = CASES_PATH,
    schema_path: Path = SCHEMA_PATH,
) -> list[dict[str, Any]]:
    """加载 JSONL，校验 schema，并拒绝不稳定的展示字段。"""

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        cases_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是合法 JSON: {exc}") from exc
        errors = sorted(validator.iter_errors(case), key=lambda error: list(error.path))
        if errors:
            detail = "; ".join(error.message for error in errors)
            raise ValueError(f"第 {line_number} 行不符合黄金 schema: {detail}")
        _validate_stable_references(case, line_number)
        cases.append(case)

    if not cases:
        raise ValueError("黄金用例不能为空")
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("黄金用例 case_id 不能重复")
    return cases


def _validate_stable_references(case: dict[str, Any], line_number: int) -> None:
    """确保绑定期望使用业务名，而不是展示别名或自由文本提示。"""

    forbidden = {"model_hint", "metric_hint", "dimension_hint", "expected_points"}
    if forbidden.intersection(case):
        raise ValueError(f"第 {line_number} 行包含不稳定展示字段")
    for group in case["binding_expect"]["groups"]:
        if not group["model_biz_name"].strip():
            raise ValueError(f"第 {line_number} 行存在空 model_biz_name")
        if any(not value.strip() for value in group["metric_biz_names"]):
            raise ValueError(f"第 {line_number} 行存在空 metric_biz_name")
        if any(not value.strip() for value in group["dimension_biz_names"]):
            raise ValueError(f"第 {line_number} 行存在空 dimension_biz_name")


def main() -> int:
    cases = load_and_validate_cases()
    print(f"validated {len(cases)} P1 golden cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
