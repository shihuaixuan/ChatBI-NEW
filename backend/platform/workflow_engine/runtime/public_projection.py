from typing import Any

from sqlbot_platform.workflow_engine.domain.definition import NodeDefinition
from sqlbot_platform.workflow_engine.domain.execution import NodeExecutionResult

TracePath = tuple[str, ...]
PUBLIC_SAMPLE_ROW_LIMIT = 5


def node_display_label(node: NodeDefinition) -> str:
    """从节点 metadata 读取前端展示名，未声明时退回稳定节点名。"""

    display = node.metadata.get("display")
    if isinstance(display, dict):
        label = display.get("label")
        if isinstance(label, str) and label.strip():
            return label.strip()
    return node.name


def node_trace_output_path(node: NodeDefinition) -> TracePath | None:
    """从节点 metadata 读取 trace 输出路径。"""

    trace = node.metadata.get("trace")
    if not isinstance(trace, dict):
        return None
    path = trace.get("output_path")
    if not isinstance(path, (list, tuple)) or not path:
        return None
    normalized = tuple(str(part) for part in path if str(part))
    return normalized or None


def public_node_summary(result: NodeExecutionResult, node: NodeDefinition) -> dict[str, Any]:
    """基于节点 trace metadata 生成事件公开摘要，与 trace API 共用同一脱敏策略。"""

    if result.interaction is not None:
        return result.interaction
    output_path = node_trace_output_path(node)
    if output_path is None:
        return _fallback_patch_summary(result)
    output = _read_patch_output(result.patch.set_values, output_path)
    return sanitize_public_output(node, output)


def sanitize_public_output(node: NodeDefinition, output: Any) -> Any:
    """按照节点 metadata 声明的策略对公开输出做投影与脱敏。"""

    trace = node.metadata.get("trace")
    redaction = trace.get("redaction") if isinstance(trace, dict) else None
    if output is None:
        return None
    if redaction == "sql_summary":
        return _sql_summary(output)
    if redaction == "split_sql_summary":
        return _split_sql_summary(output)
    if redaction == "execution_summary":
        return _execution_summary(output, node_name=node.name)
    return output


def _read_patch_output(set_values: dict[str, Any], output_path: TracePath) -> Any:
    dotted_path = ".".join(output_path)
    if dotted_path in set_values:
        return set_values[dotted_path]
    current: Any = set_values
    for part in output_path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _fallback_patch_summary(result: NodeExecutionResult) -> dict[str, Any]:
    values = result.patch.set_values
    if not values:
        return {}
    if len(values) == 1:
        value = next(iter(values.values()))
        return value if isinstance(value, dict) else {"value": value}
    return dict(values)


def _sql_summary(output: Any) -> dict[str, Any]:
    sql = output.get("sql", "") if isinstance(output, dict) else ""
    statement_type = sql.strip().split(maxsplit=1)[0].lower() if sql.strip() else "unknown"
    return {
        "statement_type": statement_type,
        "sql": sql,
        "artifact_ref": output.get("artifact_ref") if isinstance(output, dict) else None,
    }


def _split_sql_summary(output: Any) -> dict[str, Any]:
    queries = output.get("queries") if isinstance(output, dict) else None
    query_count = len(queries) if isinstance(queries, list) else 0
    return {
        "query_count": query_count,
        "queries": _query_summaries(queries),
        "artifact_ref": output.get("artifact_ref") if isinstance(output, dict) else None,
    }


def _execution_summary(output: Any, *, node_name: str) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {}
    results = output.get("results")
    if not isinstance(results, list):
        results = []
    artifact_refs = output.get("artifact_refs")
    if not isinstance(artifact_refs, list):
        artifact_refs = [
            item.get("artifact_ref")
            for item in results
            if isinstance(item, dict) and isinstance(item.get("artifact_ref"), dict)
        ]
    query_count = len(results)
    if query_count == 0 and node_name == "execute_sql":
        query_count = 1
    return {
        "status": output.get("status"),
        "query_count": query_count,
        "row_count": output.get("row_count", 0),
        "fields": output.get("fields", []),
        "execution_ms": output.get("execution_ms", 0),
        "artifact_refs": artifact_refs,
        "results": _execution_result_summaries(results),
    }


def _query_summaries(queries: Any) -> list[dict[str, Any]]:
    if not isinstance(queries, list):
        return []
    summaries: list[dict[str, Any]] = []
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            continue
        summaries.append(
            {
                "query_id": query.get("query_id") or f"query-{index}",
                "model_id": query.get("model_id"),
                "metrics": query.get("metrics") if isinstance(query.get("metrics"), list) else [],
                "sql": query.get("sql") or "",
            }
        )
    return summaries


def _execution_result_summaries(results: list[Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            continue
        # 公开事件只携带有限样本；完整结果继续通过 artifact 引用读取。
        sample_rows = result.get("sample_rows")
        if not isinstance(sample_rows, list):
            sample_rows = []
        artifact_ref = result.get("artifact_ref")
        summaries.append(
            {
                "query_id": result.get("query_id") or f"query-{index}",
                "status": result.get("status"),
                "row_count": result.get("row_count", 0),
                "fields": result.get("fields", []),
                "sample_rows": sample_rows[:PUBLIC_SAMPLE_ROW_LIMIT],
                "result_truncated": bool(result.get("result_truncated", False)),
                "execution_ms": result.get("execution_ms", 0),
                "artifact_ref": artifact_ref if isinstance(artifact_ref, dict) else None,
            }
        )
    return summaries
