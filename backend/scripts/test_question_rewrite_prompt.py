#!/usr/bin/env python3
"""使用 Orca API 测试问数问题重写提示词。"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
REQUIRED_KEYS = {"original_question", "rewrite_question"}


SYSTEM_PROMPT = """你是智能问数场景下的问题重写大师。

你的唯一任务是：根据用户当前问题和必要的会话上下文，将当前问题重写为一个完整、明确、专业的在问数场景下的自然语言问题。

重写规则：

1. 保留用户当前问题中明确表达的指标、维度、筛选条件、时间范围和展示要求。
2. 消解代词、省略和上下文引用。
3. 只有在历史上下文中存在唯一明确指向时，才能补全省略内容。
4. 当前问题是对上一轮问题的修改时，只修改用户明确要求修改的部分，保留其他未修改条件。
5. 当前问题可以独立表达完整含义时，不继承上一轮无关内容。
6. 保留会影响后续模式分类的查询动作和分析关系，包括：
   - 同时查询多个指标；
   - 比较不同时间或对象；
   - 计算差值、占比、增长率；
   - 查询趋势或环比；
   - 排名和下钻；
   - 分析原因、归因和影响因素。
7. 可以把口语化表达改写为明确的自然语言，但不能改变用户原意。
8. 可以补全明确的相对时间表达，但不能猜测无法确定的时间。
9. 保留用户原有的业务名称、指标名称、店铺编号、区域名称、渠道名称和筛选值。
10. 不得增加用户没有提出的指标、维度、筛选条件、排序、Top N、比较关系、计算关系或分析目标。
11. 不得将业务名称转换为表名、字段名、内部资产 ID 或 SQL。
12. 不得输出问题分类、意图、模式、置信度、缺失字段或解释说明。
13. 如果上下文存在多个可能指向，无法唯一确定时，不得自行猜测；rewrite_question 保留原问题中无法确定的表达，交由后续流程处理。

输出要求：

只输出一个合法 JSON 对象，且只能包含以下两个字段：

{
    "original_question": "用户本轮提交的原始问题",
    "rewrite_question": "重写后的完整问题"
}

字段要求：

- original_question 必须保留用户本轮提交的问题原文；
- rewrite_question 必须是后续流程使用的完整自然语言问题；
- 不得输出 Markdown 代码块；
- 不得输出 JSON 以外的文字；
- 不得增加其他字段。"""


TEST_CASES = [
    {
        "name": "fast_single_query",
        "question": "6月各店铺的GMV",
        "context": "没有可继承的历史会话。",
        "reference_datetime": "2026-08-18 00:00:00",
        "timezone": "Asia/Shanghai",
    },
    {
        "name": "plan_multi_metric_compute",
        "question": "6月店铺100013的GMV和未发订单数，并算一下订单金额占比",
        "context": "没有可继承的历史会话。",
        "reference_datetime": "2026-08-18 00:00:00",
        "timezone": "Asia/Shanghai",
    },
    {
        "name": "research_attribution",
        "question": "为什么华东6月GMV比5月下降？主要是哪几个店铺导致的？",
        "context": "没有可继承的历史会话。",
        "reference_datetime": "2026-08-18 00:00:00",
        "timezone": "Asia/Shanghai",
    },
    {
        "name": "context_reference",
        "question": "那店铺100021呢？",
        "context": (
            "上一轮用户问题：查询2026年6月30日店铺100013的总GMV。\n"
            "上一轮系统回答：已返回店铺100013的总GMV。"
        ),
        "reference_datetime": "2026-08-18 00:00:00",
        "timezone": "Asia/Shanghai",
    },
]


def build_user_prompt(test_case: dict[str, str]) -> str:
    """构造本次调用的用户输入。"""

    return (
        f"当前用户问题：\n{test_case['question']}\n\n"
        f"会话上下文：\n{test_case['context']}\n\n"
        f"当前时间：\n{test_case['reference_datetime']}\n\n"
        f"当前时区：\n{test_case['timezone']}"
    )


def call_model(api_key: str, test_case: dict[str, str]) -> dict[str, object]:
    """调用 Orca Chat Completions 接口并返回原始响应对象。"""

    payload = {
        "model": MODEL,
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
        raise RuntimeError(
            f"接口返回 HTTP {error.code}: {error_body}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"请求接口失败: {error.reason}") from error

    response_json = json.loads(response_body)
    content = response_json["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise RuntimeError("模型返回的 message.content 不是字符串")

    # 兼容模型偶尔返回代码块的情况，但这类结果会被校验为失败。
    cleaned_content = content.strip()
    try:
        return {
            "raw_content": content,
            "result": json.loads(cleaned_content),
        }
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"模型没有返回合法 JSON，原始内容为:\n{content}"
        ) from error


def validate_result(
    result: object,
    test_case: dict[str, str],
) -> list[str]:
    """校验问题重写结果是否符合两个字段的输出契约。"""

    errors: list[str] = []
    if not isinstance(result, dict):
        return ["输出不是 JSON 对象"]

    result_keys = set(result)
    if result_keys != REQUIRED_KEYS:
        errors.append(
            "输出字段必须严格为 "
            f"{sorted(REQUIRED_KEYS)}，实际为 {sorted(result_keys)}"
        )

    original_question = result.get("original_question")
    rewrite_question = result.get("rewrite_question")
    if original_question != test_case["question"]:
        errors.append(
            "original_question 未保留原始问题："
            f"期望 {test_case['question']!r}，实际 {original_question!r}"
        )
    if not isinstance(rewrite_question, str) or not rewrite_question.strip():
        errors.append("rewrite_question 必须是非空字符串")

    return errors


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="测试问数问题重写提示词，不执行后续分类和查询流程。"
    )
    parser.add_argument(
        "--case",
        choices=[case["name"] for case in TEST_CASES],
        help="只运行指定案例；不传时运行全部案例。",
    )
    return parser.parse_args()


def main() -> int:
    """运行问题重写提示词测试。"""

    args = parse_args()
    api_key = "sk-b8c8efc20e874d1fbff78a2ba8e94cb6"
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
        print(f"原始问题：{test_case['question']}")
        try:
            response = call_model(api_key, test_case)
            result = response["result"]
            print("模型输出：")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            errors = validate_result(result, test_case)
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
