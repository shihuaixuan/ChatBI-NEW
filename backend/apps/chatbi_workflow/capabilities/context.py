"""ChatBI v1 节点上下文的统一只读视图。

能力 adapter 收到的请求是 `ChatBIV1CapabilityNode` 组装的完整上下文视图
（request / conversation / variables / inputs / node_name）。此前每个 adapter
各自实现取值与类型防御（`isinstance(x, dict)` 层层嵌套、`_int_or_none` 三处
语义分叉），本模块把读取收敛为一个入口：

- 读侧唯一：adapter 一律通过 `ChatBIRunContext` 取值，不再直接翻字典；
- 只读：本类不提供任何写入方法，节点输出仍由 handler 的 output_path 落盘；
- 防御集中：dict 守卫与 int 宽松转换只在这里出现一次。

写侧结构（variables 的各个键）在 Step 2 之前保持不变。
"""

from __future__ import annotations

from typing import Any

from apps.chatbi_workflow.capabilities.interactions import (
    read_interaction_record,
    read_interaction_response,
)


def int_or_none(value: Any) -> int | None:
    """宽松整数转换：接受 int 与数字字符串，拒绝 bool 与其他类型。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class ChatBIRunContext:
    """一次节点调用可见上下文的只读访问器。"""

    def __init__(self, request: dict[str, Any]) -> None:
        payload = _dict(request)
        self._request = _dict(payload.get("request"))
        self._conversation = _dict(payload.get("conversation"))
        self._variables = _dict(payload.get("variables"))
        self._node_name = str(payload.get("node_name") or "")
        self._run_id = str(payload.get("run_id") or "")

    # ---- 请求身份（由服务端注入，节点不可修改） ----

    @property
    def node_name(self) -> str:
        return self._node_name

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def raw_question(self) -> str:
        return str(self._request.get("question") or "").strip()

    @property
    def dataset_id(self) -> int | None:
        return int_or_none(self._request.get("dataset_id"))

    @property
    def tenant_id(self) -> int:
        return int_or_none(self._request.get("tenant_id") or self._request.get("oid")) or 1

    @property
    def user_id(self) -> int | None:
        return int_or_none(self._request.get("user_id"))

    def request_value(self, key: str) -> Any:
        """按原样读取请求字段。仅供需要保留原始值透传的场景（如权限载荷）。"""

        return self._request.get(key)

    @property
    def conversation(self) -> dict[str, Any]:
        return self._conversation

    # ---- 派生视图 ----

    @property
    def question(self) -> str:
        """优先使用标准化后的问题，回退到原始问题。"""

        rewritten = str(self.rewrite.get("rewritten_question") or "").strip()
        return rewritten or self.raw_question

    # ---- variables 域读取（全部带 dict 守卫） ----

    @property
    def variables(self) -> dict[str, Any]:
        """全量变量视图。仅供答案生成等确需整体上下文的场景，新代码优先用具名域。"""

        return self._variables

    def domain(self, key: str) -> dict[str, Any]:
        return _dict(self._variables.get(key))

    @property
    def classification(self) -> dict[str, Any]:
        return self.domain("classification")

    @property
    def rewrite(self) -> dict[str, Any]:
        return self.domain("rewrite")

    @property
    def intent(self) -> dict[str, Any]:
        return self.domain("intent")

    @property
    def knowledge(self) -> dict[str, Any]:
        return self.domain("knowledge")

    @property
    def plan(self) -> dict[str, Any]:
        return self.domain("plan")

    @property
    def sql(self) -> dict[str, Any]:
        return self.domain("sql")

    @property
    def split_sql(self) -> dict[str, Any]:
        return self.domain("split_sql")

    @property
    def execution(self) -> dict[str, Any]:
        standard = self.domain("execution")
        return standard or self.domain("sql_execution")

    @property
    def sql_execution(self) -> dict[str, Any]:
        """兼容旧调用方；新代码统一读取 execution。"""

        return self.execution

    @property
    def sql_error(self) -> dict[str, Any]:
        return self.domain("sql_error")

    @property
    def image_profile(self) -> dict[str, Any]:
        return self.domain("image_profile")

    @property
    def answer(self) -> dict[str, Any]:
        return self.domain("answer")

    @property
    def recommendations(self) -> dict[str, Any]:
        return self.domain("recommendations")

    @property
    def node_failure(self) -> dict[str, Any]:
        return self.domain("node_failure")

    # ---- 交互回答（一次性输入，消费方式见各节点） ----

    def interaction(self, node_name: str) -> dict[str, Any]:
        return read_interaction_record(self._variables, node_name)

    def interaction_response(self, node_name: str, legacy_key: str | None = None) -> dict[str, Any]:
        return read_interaction_response(self._variables, node_name, legacy_key)

    @property
    def rewrite_response(self) -> dict[str, Any]:
        return self.interaction_response("ask_rewrite_clarification", "rewrite_response")

    @property
    def intent_response(self) -> dict[str, Any]:
        return self.interaction_response("ask_intent_clarification", "intent_response")

    @property
    def slot_response(self) -> dict[str, Any]:
        return self.interaction_response("ask_slot_clarification", "slot_response")

    @property
    def metric_selection(self) -> dict[str, Any]:
        return self.interaction_response("ask_metric_selection", "metric_selection")

    @property
    def cross_model_response(self) -> dict[str, Any]:
        return self.interaction_response("ask_cross_model_split", "cross_model_response")
