#!/usr/bin/env python3
"""使用 Orca API 测试问数语义提取提示词。"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
REASONING_EFFORT = "low"

REQUIRED_KEYS = {
    "mentions",
    "groupings",
    "expressions",
    "conditions",
    "orders",
    "unresolved",
}
MENTION_KEYS = {
    "id",
    "kind",
    "text",
    "role",
}
GROUPING_KEYS = {"ref", "grain", "raw"}
EXPRESSION_KEYS = {
    "id",
    "op",
    "text",
    "metric_refs",
    "dimension_refs",
    "time_refs",
    "outputs",
}
CONDITION_KEYS = {"target_ref", "value_refs", "operator", "raw"}
ORDER_KEYS = {"target_ref", "direction", "limit", "raw"}
UNRESOLVED_KEYS = {"text", "reason"}


SYSTEM_PROMPT = """你是智能问数场景下的语义提取专家。

你的唯一任务是：从用户问题中，根据下面的规则提取问数场景下的元信息。

输出必须是一个合法 JSON 对象，且只能包含以下字段：

{
  "mentions": [],
  "groupings": [],
  "expressions": [],
  "conditions": [],
  "orders": [],
  "unresolved": []
}

顶层字段含义：

- mentions：问题中出现的指标、维度、具体值和时间等原始语义提及，供后续资产检索使用。
- groupings：用户明确要求的分组维度或时间粒度，供后续查询分组使用。
- expressions：用户明确要求的比较、增长、差值、占比、排名或归因关系，供后续执行需求分析使用。
- conditions：用户明确提出的维度筛选或指标条件，供后续生成过滤条件使用。
- orders：用户明确提出的排序方向和数量限制，供后续生成排序和 Top N 条件使用。
- unresolved：确实存在业务歧义、且会影响后续处理的原文片段。没有歧义时必须返回空数组。

如果某类信息在问题中没有出现，对应字段必须返回空数组，不得省略字段，也不得根据常识补充信息。

一、mentions：问题中的原始语义提及

每个元素必须包含：

{
  "id": "m1",
  "kind": "metric|dimension|value|time|unknown",
  "text": "问题中的完整原文片段",
  "role": "measure|group_by|filter|display|unknown|null"
}

规则：

- id：当前提及在本次输出中的唯一引用标识，只用于被 groupings、expressions 和 conditions 引用，不是语义资产 ID。
- kind：提及的原文类型；metric 表示指标，dimension 表示维度，value 表示具体值，time 表示时间，unknown 表示无法判断类型。
- text：问题中的完整原文片段，保留用户的业务表达，不改写、不转换为内部字段。
- role：提及在当前问题中的作用；measure 表示度量，group_by 表示分组，filter 表示筛选，display 表示展示，unknown 表示无法判断，null 表示没有明确作用。
1. text 必须保留问题中的完整业务表达，不拆分为内部字段。
2. 指标提及必须保留完整业务短语，例如“销售订单平均客单价”“超时未发订单金额”“总GMV”，不能只提取“客单价”“订单”或“GMV”。
3. 维度提及包括用户要求的分组维度、筛选维度和展示维度，例如“各店铺”“交易渠道”。
4. value 表示店铺编号、区域、渠道、组织、商品、状态、数值等具体值。
5. 如果问题只出现了一个无法拆分的维度值，例如“华东地区”，不要臆造“地区”这个原文中不存在的维度；将“华东地区”作为完整 value 提取，并在 conditions 中直接引用它。
6. time 表示问题中的时间原文，例如“2026年6月”“2025年与2026年上半年”“每月”。不要在此处生成时间 AST、时间范围或绑定日期字段。
7. 同一个原文片段不要重复生成多个相同 mentions。业务短语内部的词不单独拆成新的指标。
8. role 只描述该提及在问题中的表达作用，不表示已经绑定到语义资产。

二、groupings：分组和时间粒度

每个元素必须包含：

{
  "ref": "m1",
  "grain": "店铺|交易渠道|月份|季度|年份|日|其他",
  "raw": "原文中的分组表达"
}

只提取用户明确要求的分组或粒度。例如“各店铺”“按交易渠道”“每月”。不要因为存在某个维度提及就自动增加分组。

字段含义：

- ref：被用作分组的 mention id。
- grain：分组粒度，例如店铺、交易渠道、月份、季度、年份或日。
- raw：问题中的原始分组表达。

三、expressions：指标之间或结果之间的计算关系

每个元素必须包含：

{
  "id": "e1",
  "op": "trend|compare|growth|difference|ratio|share|ranking|attribution",
  "text": "原文中的计算或分析表达",
  "metric_refs": ["m1"],
  "dimension_refs": ["m2"],
  "time_refs": ["m3"],
  "outputs": ["原文要求输出的结果"]
}

规则：

1. 只提取用户明确表达的关系：趋势、比较、增长率、差值、比例、占比、排名或归因。
2. “同比”“环比”“增长率”统一记录为 growth，并在 time_refs 中关联涉及的时间提及。
3. “占全部店铺的比例”记录为 share；“A占B的比例”需要在 outputs 中保留分子和分母的原文含义。不要把“全部店铺”单独识别成新的维度。
4. “为什么下降”“哪些店铺导致变化”记录为 attribution，但不要在此处推断原因或贡献对象。
5. “增长率”“变化量”“占比”等由表达式计算出来的结果，不要作为 metric mention；只放入 expressions.outputs。
6. 完整的业务指标短语如果本身没有明确的计算关系，例如“销售订单平均客单价”，只提取为一个完整 metric mention，不要擅自拆成分子、分母或基础指标。
7. 计算关系引用 mentions 中已经提取的 id；不能编造资产 ID。

字段含义：

- id：当前计算关系的唯一引用标识，不是执行计划节点 ID。
- op：计算关系类型。
- text：问题中表达该关系的完整原文片段。
- metric_refs：参与计算的指标 mention id。
- dimension_refs：参与分析或归因的维度 mention id。
- time_refs：参与比较或计算的时间 mention id。
- outputs：用户要求输出的计算结果名称，例如变化量、增长率或比例。

四、conditions：筛选条件和指标条件

每个元素必须包含：

{
  "target_ref": "m1",
  "value_refs": ["m2"],
  "operator": "=|!=|>|>=|<|<=|in|not_in|contains|unknown",
  "raw": "原文中的完整条件"
}

提取用户明确表达的普通筛选和指标条件，例如“店铺100013”“华东地区”“GMV高于10000元”。条件中的维度、指标和具体值必须通过 mentions 的 id 引用。

除时间外，条件中的每个具体值都必须先作为 value mention 提取，再通过 value_refs 引用；不要把数值或文本值直接写在 condition 中。

如果原文中没有明确维度名称，例如“华东地区”，可以将该 value mention 直接作为 target_ref，value_refs 为空；不得凭空生成“地区”等原文中没有出现的维度。

时间不是普通筛选条件。所有时间条件只保留在 mentions 和 expressions.time_refs 中，不能放入 conditions。时间的归一化、补全年份、相对时间计算由后续时间解析模块完成。

不要在此处决定条件属于 WHERE 还是 HAVING，也不要把自然语言值转换为内部枚举或字段值。

字段含义：

- target_ref：被筛选的指标、维度，或在原文中无法拆分时的完整 value mention id。
- value_refs：条件值 mention id 列表；没有明确维度名称且 target_ref 已经是完整 value 时为空数组。
- operator：条件运算符。
- raw：问题中的完整筛选表达。

五、orders：排序和数量限制

每个元素必须包含：

{
  "target_ref": "m1",
  "direction": "asc|desc|unknown",
  "limit": null,
  "raw": "原文中的排序表达"
}

只有用户明确要求排序或 Top N 时才提取。没有数量限制时 limit 必须为 null。

字段含义：

- target_ref：被排序的指标或维度 mention id。
- direction：升序、降序或无法判断。
- limit：用户明确要求的数量限制，没有限制时为 null。
- raw：问题中的完整排序表达。

六、unresolved：无法安全确定但会影响后续流程的内容

每个元素必须包含：

{
  "text": "原文片段",
  "reason": "无法确定的原因"
}

只有存在真实的业务歧义或无法判断语义类型时才记录。不要把“尚未绑定资产”或“时间尚未归一化”当作 unresolved；资产绑定和时间解析由后续流程完成。

字段含义：

- text：存在歧义的原文片段。
- reason：无法安全确定的具体原因，不要给出猜测结果。

严格禁止：

1. 不输出 asset_id、model_id、dataset_id、table、field、SQL、QueryTask、AnalysisPlan 或任何内部字段。
2. 不输出 category、intent_type、query_shape、mode、confidence 等分类或路由字段。
3. 不生成未在问题中出现的指标、维度、筛选值、时间范围、排序、Top N 或计算关系。
4. 不根据常识猜测指标口径、日期字段、时间起止日期、同比基期或业务原因。
5. 不把一个复杂业务指标拆成未经用户表达的内部基础指标。比如“销售订单平均客单价”只作为一个完整指标提及，后续资产绑定和执行需求分析再决定是否需要分子、分母。
6. 不输出 start、end、attached_to 等原文位置或提及关联字段。
7. 不输出 Markdown 代码块，不输出 JSON 以外的解释文字。

请只返回符合上述契约的 JSON 对象。"""


TEST_CASES = [
    {
        "name": "fast_single_metric",
        "question": "2026年6月各店铺的总GMV是多少？",
    },
    {
        "name": "plan_comparison_growth",
        "question": "店铺100023在2026年6月30日的总GMV相比6月29日变化了多少，增长率是多少？",
    },
    {
        "name": "plan_share",
        "question": "2026年6月各店铺总GMV占全部店铺总GMV的比例是多少？",
    },
    {
        "name": "plan_derived_metric",
        "question": "2026年6月各店铺销售订单平均客单价是多少？",
    },
    {
        "name": "plan_ranking_condition",
        "question": "查询2026年6月总GMV高于10000元的店铺，并按GMV降序排列取前10名。",
    },
    {
        "name": "research_attribution",
        "question": "为什么华东地区2026年6月GMV比5月下降，主要是哪几个店铺导致的？",
    },
]


def build_user_prompt(test_case: dict[str, str]) -> str:
    """构造已经完成问题重写的语义提取输入。"""

    return (
        "请提取下面问题中的结构化语义信息。\n\n"
        f"重写后的问题：\n{test_case['question']}"
    )


def call_model(api_key: str, test_case: dict[str, str]) -> dict[str, object]:
    """调用 Orca Chat Completions 接口并解析模型输出。"""

    payload = {
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(test_case)},
        ],
    }
    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"接口返回 HTTP {error.code}: {error_body}") from error
    except URLError as error:
        raise RuntimeError(f"请求接口失败: {error.reason}") from error

    response_json = json.loads(response_body)
    content = response_json["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise RuntimeError("模型返回的 message.content 不是字符串")

    try:
        return {"raw_content": content, "result": json.loads(content.strip())}
    except json.JSONDecodeError as error:
        raise RuntimeError(f"模型没有返回合法 JSON，原始内容为：\n{content}") from error


def validate_item_keys(
    items: object,
    expected_keys: set[str],
    field_name: str,
    errors: list[str],
) -> None:
    """校验数组元素是否使用约定字段，避免测试时静默接受错误结构。"""

    if not isinstance(items, list):
        errors.append(f"{field_name} 必须是数组")
        return

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{field_name}[{index}] 必须是对象")
            continue
        actual_keys = set(item)
        if actual_keys != expected_keys:
            errors.append(
                f"{field_name}[{index}] 字段必须为 {sorted(expected_keys)}，"
                f"实际为 {sorted(actual_keys)}"
            )


def validate_result(result: object) -> list[str]:
    """校验语义提取结果的顶层和嵌套字段。"""

    errors: list[str] = []
    if not isinstance(result, dict):
        return ["输出不是 JSON 对象"]

    actual_keys = set(result)
    if actual_keys != REQUIRED_KEYS:
        errors.append(
            f"顶层字段必须为 {sorted(REQUIRED_KEYS)}，实际为 {sorted(actual_keys)}"
        )

    validate_item_keys(result.get("mentions"), MENTION_KEYS, "mentions", errors)
    validate_item_keys(result.get("groupings"), GROUPING_KEYS, "groupings", errors)
    validate_item_keys(
        result.get("expressions"), EXPRESSION_KEYS, "expressions", errors
    )
    validate_item_keys(
        result.get("conditions"), CONDITION_KEYS, "conditions", errors
    )
    validate_item_keys(result.get("orders"), ORDER_KEYS, "orders", errors)
    validate_item_keys(
        result.get("unresolved"), UNRESOLVED_KEYS, "unresolved", errors
    )
    validate_references(result, errors)
    return errors


def validate_references(result: dict[str, object], errors: list[str]) -> None:
    """校验各结构之间的引用，并阻止时间错误进入普通条件。"""

    mentions = result.get("mentions")
    if not isinstance(mentions, list):
        return

    mention_ids: set[str] = set()
    time_ids: set[str] = set()
    for index, mention in enumerate(mentions):
        if not isinstance(mention, dict):
            continue
        mention_id = mention.get("id")
        if not isinstance(mention_id, str):
            continue
        if mention_id in mention_ids:
            errors.append(f"mentions[{index}] 的 id 重复：{mention_id}")
        mention_ids.add(mention_id)
        if mention.get("kind") == "time":
            time_ids.add(mention_id)

    groupings = result.get("groupings")
    if isinstance(groupings, list):
        for index, grouping in enumerate(groupings):
            if isinstance(grouping, dict) and grouping.get("ref") not in mention_ids:
                errors.append(
                    f"groupings[{index}].ref 引用了不存在的 mention："
                    f"{grouping.get('ref')}"
                )

    expressions = result.get("expressions")
    if isinstance(expressions, list):
        expression_ids: set[str] = set()
        for index, expression in enumerate(expressions):
            if not isinstance(expression, dict):
                continue
            expression_id = expression.get("id")
            if isinstance(expression_id, str):
                if expression_id in expression_ids:
                    errors.append(
                        f"expressions[{index}] 的 id 重复：{expression_id}"
                    )
                expression_ids.add(expression_id)
            for field_name in ("metric_refs", "dimension_refs", "time_refs"):
                refs = expression.get(field_name)
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    if ref not in mention_ids:
                        errors.append(
                            f"expressions[{index}].{field_name} 引用了不存在的 mention：{ref}"
                        )

    conditions = result.get("conditions")
    if isinstance(conditions, list):
        for index, condition in enumerate(conditions):
            if not isinstance(condition, dict):
                continue
            target_ref = condition.get("target_ref")
            if target_ref not in mention_ids:
                errors.append(
                    f"conditions[{index}].target_ref 引用了不存在的 mention：{target_ref}"
                )
            if target_ref in time_ids:
                errors.append(
                    f"conditions[{index}] 不允许把时间 mention 作为普通条件：{target_ref}"
                )
            value_refs = condition.get("value_refs")
            if isinstance(value_refs, list):
                for ref in value_refs:
                    if ref not in mention_ids:
                        errors.append(
                            f"conditions[{index}].value_refs 引用了不存在的 mention：{ref}"
                        )

    orders = result.get("orders")
    if isinstance(orders, list):
        for index, order in enumerate(orders):
            if isinstance(order, dict) and order.get("target_ref") not in mention_ids:
                errors.append(
                    f"orders[{index}].target_ref 引用了不存在的 mention："
                    f"{order.get('target_ref')}"
                )


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="测试问数语义提取提示词。")
    parser.add_argument(
        "--case",
        choices=[case["name"] for case in TEST_CASES],
        help="只运行指定案例；不传时运行全部案例。",
    )
    return parser.parse_args()


def main() -> int:
    """运行语义提取提示词测试。"""

    args = parse_args()
    api_key = "sk-4b3b6498ecec4853a11a51a4b9dc379c"
    if not api_key:
        print(
            "缺少 ORCA_API_KEY，请先执行："
            "export ORCA_API_KEY='你的 Orca API Key'",
            file=sys.stderr,
        )
        return 2

    selected_cases = [
        case for case in TEST_CASES
        if args.case is None or case["name"] == args.case
    ]
    failed_count = 0

    for test_case in selected_cases:
        print(f"\n===== {test_case['name']} =====")
        print(f"问题：{test_case['question']}")
        try:
            response = call_model(api_key, test_case)
            result = response["result"]
            print("模型输出：")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            errors = validate_result(result)
        except (RuntimeError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            errors = [str(error)]

        if errors:
            failed_count += 1
            print("结果：FAIL")
            for error in errors:
                print(f"- {error}")
        else:
            print("结果：PASS")

    print(f"\n完成：{len(selected_cases) - failed_count}/{len(selected_cases)}")
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
