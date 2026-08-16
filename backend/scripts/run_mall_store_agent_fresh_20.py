"""从零执行商城店铺数据集的 20 个 Agent 全链路问题。

本脚本只复用公开 HTTP 契约，不导入历史测试问题或历史测试结果。
每个问题使用独立会话；若出现澄清，则按澄清树逐层覆盖全部候选。
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session

from apps.access_control.composition import build_identity_workspace_service
from common.core.config import settings
from common.core.db import engine
from common.core.security import create_access_token


@dataclass(frozen=True, slots=True)
class FreshQuestion:
    """本轮新设计的问题及数据库基准关键值。"""

    case_id: str
    question: str
    coverage: str
    expected_text: tuple[str, ...] = ()


# 问题使用当前源数据中的新对象和新分析组合，不复用历史测试问题。
QUESTIONS = (
    FreshQuestion(
        "N01",
        "2026年6月10日，店铺100013的销售订单数、订货订单数和总订单数各是多少？",
        "同模型多指标、单日、单店铺",
        ("7", "0"),
    ),
    FreshQuestion(
        "N02",
        "2026年6月8日至14日，店铺100021哪一天的总GMV最高？请给出日期和金额。",
        "日期区间、最大值、Top1",
        ("2026-06-08", "40407.48"),
    ),
    FreshQuestion(
        "N03",
        "2026年6月30日，对比店铺100022和100023的总商品件数，并指出较高的店铺。",
        "双店铺筛选、分组、比较",
        ("100022", "57", "100023", "50"),
    ),
    FreshQuestion(
        "N04",
        "列出2026年6月店铺100013订货类GMV最高的3天，按金额从高到低展示。",
        "月范围、排序、Top3、订货指标",
        ("2026-06-19", "24934.87", "2026-06-12", "13562.81"),
    ),
    FreshQuestion(
        "N05",
        "2026年6月，店铺100021有哪些日期的订货类GMV高于销售类GMV？列出两项金额。",
        "指标间条件、日期明细、月范围",
        ("2026-06-12", "2026-06-20", "2026-06-26"),
    ),
    FreshQuestion(
        "N06",
        "2026年6月30日，店铺100022的销售订单平均客单价和订单平均客单价分别是多少？",
        "两个派生指标、单日、单店铺",
        ("3398.634", "2457.93375"),
    ),
    FreshQuestion(
        "N07",
        "店铺100023在2026年6月30日的总GMV相比6月29日减少了多少，降幅是多少？",
        "两日对比、差值、变化率",
        ("11358.72", "7621.32", "3737.4"),
    ),
    FreshQuestion(
        "N08",
        "展示2026年6月24日至30日店铺100013的总下单客户数每日变化。",
        "七日趋势、时间粒度、客户指标",
        ("2026-06-24", "2026-06-30", "3"),
    ),
    FreshQuestion(
        "N09",
        "在2026年6月30日未发订单快照中，店铺100021超时天数最多的订单号、客户ID和超时天数是什么？",
        "快照、Top1、订单与客户维度",
        ("USO202606300004", "C1000201007", "13"),
    ),
    FreshQuestion(
        "N10",
        "2026年6月30日店铺100022所有超时未发订单合计有多少未发件数和订单金额？",
        "布尔筛选、快照聚合、双指标",
        ("16", "14780"),
    ),
    FreshQuestion(
        "N11",
        "2026年6月30日店铺100023未发件数最多的3笔订单是什么？展示订单号、商品摘要和未发件数。",
        "快照明细、排序、Top3",
        ("USO202606300018", "12", "USO202606300012", "11"),
    ),
    FreshQuestion(
        "N12",
        "2026年6月30日店铺100021历史总销量最高的3个商品ID是什么？同时给出历史总销量和当前库存件数。",
        "库存快照、Top3、三列结果",
        ("P1000201016", "19341", "61", "P1000201023"),
    ),
    FreshQuestion(
        "N13",
        "找出2026年6月30日店铺100022的负库存商品，列出商品ID、商品名称和当前库存件数。",
        "状态筛选、商品明细、负数值",
        ("P1000202025", "复古牛仔夹克", "-5"),
    ),
    FreshQuestion(
        "N14",
        "2026年6月30日店铺100023有多少个连续30天未动销商品？其中库存最多的商品ID和库存件数是多少？",
        "滞销指标、条件内Top1、复合回答",
        ("9", "P1000203006", "479"),
    ),
    FreshQuestion(
        "N15",
        "按线上和线下拆分2026年6月30日店铺100013的客户当日GMV与客户当日订单数。",
        "渠道分组、客户交易、多指标",
        ("线上", "28906.32", "8", "线下", "13560.48", "4"),
    ),
    FreshQuestion(
        "N16",
        "统计2026年6月店铺100021累计客户GMV最高的5个客户ID，按累计金额降序排列。",
        "跨日聚合、客户Top5、月范围",
        ("C1000201004", "74808.38", "C1000201006", "59583.43"),
    ),
    FreshQuestion(
        "N17",
        "2026年6月7日店铺100022有多少首单成交客户？这些客户的GMV合计是多少？",
        "首单状态筛选、去重计数、金额聚合",
        ("2", "6630.3"),
    ),
    FreshQuestion(
        "N18",
        "2026年6月30日店铺100023的逾期客户数、当前欠款余额合计和逾期欠款金额合计分别是多少？",
        "欠款快照、多指标、状态口径",
        ("6", "483545", "175179.45"),
    ),
    FreshQuestion(
        "N19",
        "店铺100022的总GMV是多少？",
        "缺少时间范围、时间澄清",
    ),
    FreshQuestion(
        "N20",
        "2026年6月30日店铺100023有多少位客户？",
        "客户口径歧义、指标澄清",
    ),
)

# N20 在本轮不同 Run 中出现过两套首层候选，目录取本轮观测并集。
N20_OBSERVED_ROOT_OPTIONS = (
    {"label": "销售下单客户数", "value": "METRIC:272:246|DIMENSION:278:246"},
    {"label": "档口ID（stall_id）", "value": "DIMENSION:278:246"},
    {"label": "档口ID（stall_id）", "value": "DIMENSION:283:247"},
    {"label": "档口ID（stall_id）", "value": "DIMENSION:296:248"},
    {"label": "档口ID（stall_id）", "value": "DIMENSION:306:249"},
    {"label": "档口ID（stall_id）", "value": "DIMENSION:315:250"},
)


def _unwrap(payload: Any) -> Any:
    """移除统一响应包装。"""

    if isinstance(payload, dict) and {"code", "data", "msg"} <= payload.keys():
        return payload["data"]
    return payload


def _flatten(value: Any) -> str:
    """把嵌套答案转为便于核验的文本。"""

    if isinstance(value, dict):
        return " ".join(_flatten(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_flatten(item) for item in value)
    return str(value or "")


def _sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    """解析流式接口的 JSON 数据帧。"""

    events: list[dict[str, Any]] = []
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        raw = line.removeprefix("data:").strip()
        if raw:
            events.append(json.loads(raw))
    return events


def _options(event: dict[str, Any]) -> list[dict[str, Any]]:
    """读取结构化澄清候选。"""

    raw = event.get("options")
    content = event.get("content") or {}
    if not isinstance(raw, list) and isinstance(content, dict):
        raw = content.get("options")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        option = item if isinstance(item, dict) else {"label": str(item), "value": str(item)}
        if option.get("label") or option.get("value"):
            result.append({"label": option.get("label"), "value": option.get("value")})
    return result


def _option_key(option: dict[str, Any]) -> str:
    """生成澄清路径中的稳定候选标识。"""

    value = option.get("value")
    if value is not None:
        return str(value)
    return json.dumps(option, ensure_ascii=False, sort_keys=True)


def _contains_expected(answer: str, expected: str) -> bool:
    """兼容千分位、尾零和百分号等展示差异。"""

    compact_answer = answer.replace(",", "")
    if expected.replace(",", "") in compact_answer:
        return True
    try:
        expected_number = Decimal(expected.replace(",", ""))
    except InvalidOperation:
        return False
    for token in compact_answer.split():
        stripped = token.strip("|:%，。；,()（）[]")
        try:
            if Decimal(stripped) == expected_number:
                return True
        except InvalidOperation:
            continue
    return False


class AgentHttpClient:
    """本轮真实 Agent HTTP 客户端。"""

    def __init__(self) -> None:
        base_url = os.getenv("AGENT_API_BASE_URL", "http://127.0.0.1:8000/api/v1")
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=None)
        self.headers = {"Accept-Language": "zh-CN"}

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, headers=self.headers, **kwargs)
        response.raise_for_status()
        return _unwrap(response.json())

    def login(self) -> None:
        token = os.getenv("AGENT_API_TOKEN", "").strip()
        if not token:
            username = os.getenv("AGENT_API_USERNAME", "admin").strip()
            password = os.getenv("AGENT_API_PASSWORD", settings.DEFAULT_PWD)
            try:
                payload = self.request(
                    "POST",
                    "/login/access-token",
                    data={"username": username, "password": password},
                )
                token = str(payload.get("access_token") or "")
            except httpx.HTTPStatusError:
                # 本地流程测试不重置用户密码，只读身份并签发短期测试令牌。
                if os.getenv("AGENT_ALLOW_LOCAL_TOKEN", "1").lower() not in {
                    "1",
                    "true",
                    "yes",
                }:
                    raise
                with Session(engine) as session:
                    user = build_identity_workspace_service(session).get_user_by_account(
                        username
                    )
                if user is None:
                    raise RuntimeError(f"本地用户不存在：{username}")
                token = create_access_token(
                    user.to_dict(), expires_delta=timedelta(hours=2)
                )
        if not token:
            raise RuntimeError("登录接口未返回访问令牌")
        self.headers["X-SQLBOT-TOKEN"] = f"Bearer {token}"

    def find_dataset(self) -> dict[str, Any]:
        datasets = self.request("GET", "/semantic/datasets")
        matches = [item for item in datasets if item.get("name") == "商城店铺数据集"]
        if len(matches) != 1:
            raise RuntimeError(f"商城店铺数据集未唯一匹配：{matches!r}")
        return matches[0]

    def create_chat(self, dataset_id: int) -> int:
        payload = self.request(
            "POST",
            "/chat/start",
            json={"dataset_id": dataset_id, "question": "商城店铺新20题流程测试"},
        )
        return int(payload["id"])

    def stream(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        with self.client.stream(
            "POST",
            "/chat/agent/stream",
            headers=self.headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            return _sse_events(response)

    def timeline(self, record_id: int) -> dict[str, Any]:
        return self.request("GET", f"/chat/agent/record/{record_id}/timeline")

    def trace(self, record_id: int) -> dict[str, Any]:
        return self.request("GET", f"/chat/agent/record/{record_id}/trace")

    def replay(self, run_id: int) -> dict[str, Any]:
        return self.request(
            "GET", f"/chat/agent/runs/{run_id}/events", params={"after_sequence": 0}
        )


def _persisted_clarification(
    client: AgentHttpClient,
    events: list[dict[str, Any]],
    required: dict[str, Any],
) -> dict[str, Any]:
    """SSE 未携带候选时，从同一 Run 的事件日志读取完整载荷。"""

    if _options(required):
        return required
    run_id = next((int(event["run_id"]) for event in events if event.get("run_id")), 0)
    if not run_id:
        return required
    persisted = client.replay(run_id).get("events") or []
    return next(
        (
            event
            for event in persisted
            if event.get("domain") == "clarification.required"
            and event.get("sequence") == required.get("sequence")
        ),
        required,
    )


def _run_path(
    client: AgentHttpClient,
    case: FreshQuestion,
    dataset_id: int,
    choice_path: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """执行一条澄清路径；未显式指定的下一轮暂取首项以发现后续候选。"""

    started_at = time.monotonic()
    chat_id = client.create_chat(dataset_id)
    events = client.stream({"action": "start", "chat_id": chat_id, "question": case.question})
    batches = [list(events)]
    pending = events
    rounds: list[dict[str, Any]] = []
    api_errors: list[str] = []
    path_mismatch = False
    for depth in range(2):
        required = next(
            (event for event in pending if event.get("domain") == "clarification.required"),
            None,
        )
        if required is None:
            break
        required = _persisted_clarification(client, events, required)
        options = _options(required)
        explicit = depth < len(choice_path)
        selected = options[0] if options else None
        if explicit:
            expected_key = _option_key(choice_path[depth])
            selected = next(
                (option for option in options if _option_key(option) == expected_key),
                None,
            )
            if selected is None:
                path_mismatch = True
        rounds.append(
            {
                "depth": depth + 1,
                "question": required.get("question")
                or (required.get("content") or {}).get("question"),
                "options": options,
                "selected": selected,
                "selection_explicit": explicit,
                "expected_selection": choice_path[depth] if explicit else None,
            }
        )
        if path_mismatch:
            break
        record_id = int(required.get("record_id") or 0)
        if not record_id:
            break
        answer = (
            {"selections": [selected]}
            if selected is not None
            else {"selections": [], "text": "请按问题涉及的业务口径继续"}
        )
        try:
            resumed = client.stream(
                {"action": "resume", "record_id": record_id, "clarification": answer}
            )
        except httpx.HTTPStatusError as exc:
            api_errors.append(f"resume HTTP {exc.response.status_code}: {exc}")
            break
        events.extend(resumed)
        batches.append(list(resumed))
        pending = resumed

    record_id = next((int(event["record_id"]) for event in events if event.get("record_id")), 0)
    run_id = next((int(event["run_id"]) for event in events if event.get("run_id")), 0)
    if not record_id or not run_id:
        raise RuntimeError(f"{case.case_id} 未获得 record_id/run_id")
    timeline = client.timeline(record_id)
    trace = client.trace(record_id)
    replay = client.replay(run_id)
    replay_events = replay.get("events") or []
    domains = [str(event.get("domain")) for event in events]
    answer_text = _flatten(
        [event.get("content") for event in events if event.get("domain") == "answer.completed"]
    )
    expected_hits = [
        value for value in case.expected_text if _contains_expected(answer_text, value)
    ]
    replay_last = max((int(event.get("sequence") or 0) for event in replay_events), default=0)
    stream_last = max((int(event.get("sequence") or 0) for event in events), default=0)
    nodes = trace.get("nodes") or []
    failure = next(
        (event for event in reversed(events) if event.get("domain") == "run.failed"), {}
    )
    generated_sql = [
        event.get("content") for event in events if event.get("domain") == "sql.generated"
    ]
    executed_sql = [
        event.get("content") for event in events if event.get("domain") == "sql.executed"
    ]
    return {
        "case_id": case.case_id,
        "question": case.question,
        "coverage": case.coverage,
        "choice_path": list(choice_path),
        "choice_path_matched": not path_mismatch
        and len(rounds) >= len(choice_path),
        "chat_id": chat_id,
        "record_id": record_id,
        "run_id": run_id,
        "elapsed_ms": round((time.monotonic() - started_at) * 1000),
        "status": timeline.get("status"),
        "error_class": timeline.get("error_class"),
        "failure_message": failure.get("content"),
        "api_errors": api_errors,
        "event_domains": domains,
        "sse_event_count": len(events),
        "has_query_execution": "sql.executed" in domains,
        "has_answer": "answer.completed" in domains,
        "answer_text": answer_text,
        "expected_text": list(case.expected_text),
        "expected_hits": expected_hits,
        "expected_pass": len(expected_hits) == len(case.expected_text),
        "clarification_rounds": rounds,
        "timeline_step_count": len(timeline.get("steps") or []),
        "timeline_tool_count": len(timeline.get("tool_calls") or []),
        "timeline": timeline,
        "generated_sql_events": generated_sql,
        "executed_sql_events": executed_sql,
        "trace_available": bool(trace.get("available")),
        "trace_node_count": len(nodes),
        "trace_node_types": sorted(
            {str(node.get("node_type")) for node in nodes if isinstance(node, dict)}
        ),
        "trace_overview": trace.get("overview") or {},
        "replay_event_count": len(replay_events),
        "replay_complete": replay_last >= stream_last,
        "batch_sequences_ordered": all(
            [int(event.get("sequence") or 0) for event in batch]
            == sorted(int(event.get("sequence") or 0) for event in batch)
            for batch in batches
        ),
    }


def _run_all_clarifications(
    client: AgentHttpClient,
    case: FreshQuestion,
    dataset_id: int,
    primary: dict[str, Any],
) -> list[dict[str, Any]]:
    """广度优先展开两层澄清，保证每个父路径下的候选均被选择。"""

    rounds = primary.get("clarification_rounds") or []
    if not rounds:
        return []
    root_options = list(rounds[0].get("options") or [])
    if case.case_id == "N20":
        known_keys = {_option_key(option) for option in root_options}
        root_options.extend(
            option
            for option in N20_OBSERVED_ROOT_OPTIONS
            if _option_key(option) not in known_keys
        )
    queue: deque[tuple[dict[str, Any], ...]] = deque(
        [(option,) for option in root_options]
    )
    seen: set[tuple[str, ...]] = set()
    branches: list[dict[str, Any]] = []
    while queue:
        path = queue.popleft()
        key = tuple(_option_key(option) for option in path)
        if key in seen:
            continue
        seen.add(key)
        mismatched_attempts: list[dict[str, Any]] = []
        branch: dict[str, Any] | None = None
        for _ in range(5):
            candidate = _run_path(client, case, dataset_id, path)
            if candidate.get("choice_path_matched"):
                branch = candidate
                break
            mismatched_attempts.append(
                {
                    "run_id": candidate.get("run_id"),
                    "record_id": candidate.get("record_id"),
                    "status": candidate.get("status"),
                    "clarification_rounds": candidate.get("clarification_rounds"),
                }
            )
            branch = candidate
        assert branch is not None
        branch["path_mismatch_attempts"] = mismatched_attempts
        branch["branch_label"] = " → ".join(
            str(option.get("label") or option.get("value") or "") for option in path
        )
        branches.append(branch)
        branch_rounds = branch.get("clarification_rounds") or []
        next_depth = len(path)
        if next_depth < 2 and len(branch_rounds) > next_depth:
            for option in branch_rounds[next_depth].get("options") or []:
                queue.append(path + (option,))
    return branches


def _write_outputs(summary: dict[str, Any], output_dir: Path) -> None:
    """写出机器明细和本次运行概览。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mall_store_agent_fresh_20_results_2026-08-15.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    cases = summary["cases"]
    all_runs = summary["all_runs"]
    rows = [
        "# 商城店铺 Agent 新 20 题运行概览",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 数据集：{summary['dataset_name']}（ID {summary['dataset_id']}）",
        f"- 主问题：{len(cases)}",
        f"- 澄清分支：{sum(len(item['clarification_branches']) for item in cases)}",
        f"- 实际 Run：{len(all_runs)}",
        "",
        "| 问题 | 终态 | SQL执行 | 最终答案 | 基准命中 | 澄清轮数 | 分支数 | 耗时ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in cases:
        primary = item["primary"]
        rows.append(
            f"| {item['case_id']} | {primary['status']} | "
            f"{'是' if primary['has_query_execution'] else '否'} | "
            f"{'是' if primary['has_answer'] else '否'} | "
            f"{len(primary['expected_hits'])}/{len(primary['expected_text'])} | "
            f"{len(primary['clarification_rounds'])} | "
            f"{len(item['clarification_branches'])} | {primary['elapsed_ms']} |"
        )
    rows.extend(
        [
            "",
            "## 澄清覆盖",
            "",
            "| 问题 | 路径 | 终态 | SQL执行 | 最终答案 | API错误 |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for item in cases:
        for branch in item["clarification_branches"]:
            rows.append(
                f"| {item['case_id']} | {branch['branch_label']} | {branch['status']} | "
                f"{'是' if branch['has_query_execution'] else '否'} | "
                f"{'是' if branch['has_answer'] else '否'} | "
                f"{'有' if branch['api_errors'] else '无'} |"
            )
    rows.extend(
        [
            "",
            "## 汇总",
            "",
            f"- 主问题执行 SQL：{sum(item['primary']['has_query_execution'] for item in cases)}/{len(cases)}",
            f"- 主问题产生答案：{sum(item['primary']['has_answer'] for item in cases)}/{len(cases)}",
            f"- 主问题全部基准关键值命中：{sum(item['primary']['expected_pass'] for item in cases if item['primary']['expected_text'])}/{sum(bool(item['primary']['expected_text']) for item in cases)}",
            f"- Run 事件补拉完整：{sum(item['replay_complete'] for item in all_runs)}/{len(all_runs)}",
            f"- Run 内 SSE 批次序号有序：{sum(item['batch_sequences_ordered'] for item in all_runs)}/{len(all_runs)}",
        ]
    )
    (output_dir / "mall_store_agent_fresh_20_overview_2026-08-15.md").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )


def main() -> None:
    """执行 20 个主问题及其全部澄清路径。"""

    client = AgentHttpClient()
    try:
        client.login()
        dataset = client.find_dataset()
        dataset_id = int(dataset["id"])
        output_dir = Path(
            os.getenv(
                "AGENT_TEST_OUTPUT_DIR",
                str(Path(__file__).parents[2] / "docs" / "test-results"),
            )
        )
        json_path = output_dir / "mall_store_agent_fresh_20_results_2026-08-15.json"
        previous_cases: dict[str, dict[str, Any]] = {}
        retry_failed = os.getenv("AGENT_RETRY_FAILED", "").lower() in {
            "1",
            "true",
            "yes",
        }
        force_cases = {
            item.strip()
            for item in os.getenv("AGENT_FORCE_CASES", "").split(",")
            if item.strip()
        }
        if retry_failed and json_path.exists():
            previous = json.loads(json_path.read_text(encoding="utf-8"))
            previous_cases = {item["case_id"]: item for item in previous["cases"]}
        case_results: list[dict[str, Any]] = []
        all_runs: list[dict[str, Any]] = []
        for index, case in enumerate(QUESTIONS, start=1):
            previous_case = previous_cases.get(case.case_id)
            previous_primary = previous_case.get("primary") if previous_case else None
            previous_failure = (
                previous_primary.get("failure_message")
                if isinstance(previous_primary, dict)
                else None
            )
            previous_details = (
                previous_failure.get("error_details")
                if isinstance(previous_failure, dict)
                else None
            )
            should_retry = case.case_id in force_cases or (
                retry_failed
                and isinstance(previous_details, dict)
                and previous_details.get("exception_type") == "APIConnectionError"
            )
            if previous_case is not None and not should_retry:
                case_results.append(previous_case)
                all_runs.extend(
                    [
                        previous_case["primary"],
                        *previous_case.get("clarification_branches", []),
                    ]
                )
                print(
                    f"[{index:02d}/{len(QUESTIONS)}] {case.case_id} 保留首轮结果",
                    flush=True,
                )
                continue
            print(f"[{index:02d}/{len(QUESTIONS)}] {case.case_id} {case.question}", flush=True)
            primary = _run_path(client, case, dataset_id, ())
            branches = _run_all_clarifications(client, case, dataset_id, primary)
            case_result = {
                "case_id": case.case_id,
                "question": case.question,
                "coverage": case.coverage,
                "primary": primary,
                "clarification_branches": branches,
            }
            if previous_primary is not None:
                history = list(previous_case.get("retry_history") or [])
                history.append(
                    {
                        "run_id": previous_primary.get("run_id"),
                        "record_id": previous_primary.get("record_id"),
                        "status": previous_primary.get("status"),
                        "error_class": previous_primary.get("error_class"),
                        "failure_message": previous_primary.get("failure_message"),
                        "elapsed_ms": previous_primary.get("elapsed_ms"),
                    }
                )
                case_result["retry_history"] = history
            case_results.append(case_result)
            all_runs.extend([primary, *branches])
        summary = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "dataset_name": dataset["name"],
            "dataset_id": dataset_id,
            "question_source": "本轮基于当前语义资产和源数据从零设计",
            "cases": case_results,
            "all_runs": all_runs,
        }
        _write_outputs(summary, output_dir)
        print(f"完成：{len(case_results)} 个主问题，{len(all_runs)} 个 Run", flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
