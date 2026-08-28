"""Research Agent 的最终回答生成边界。

Responder 只消费已经通过 ``finish_research`` 校验的 Completion、确认结论和
Evidence。它不持有研究工具，也不根据用户问题重新查询数据；回答中的表格、
图表和限制都从传入的受控事实生成。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.chatbi.models.dto.research_agent import (
    Completion,
    CompletionLimitation,
    Evidence,
    Finding,
)

RESEARCH_RESPONSE_KEY = "research_response"

_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_:])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
)
_IDENTIFIER_PATTERN = re.compile(
    r"(?:ASSET|METRIC|DIMENSION|FILTER|MODEL|HIERARCHY|RELATION|"
    r"LOGICAL_METRIC|LOGICAL_DIMENSION|evidence):[A-Za-z0-9_.:-]+"
)
_DATE_PATTERN = re.compile(
    r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日|"
    r"\d{1,2}月\d{1,2}日)"
)


class ResearchResponderError(ValueError):
    """回答输入不满足证据约束时返回的明确错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}:{message}")


class ResearchChartDataSource(BaseModel):
    """审计一张图表实际使用的 Evidence 列。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    column_refs: tuple[str, ...] = ()


class ResearchResponseAudit(BaseModel):
    """最终回答的最小审计载荷。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    chart_data_sources: tuple[ResearchChartDataSource, ...] = ()
    final_status: Literal["complete", "partial", "unanswerable"]


class ResearchResponse(BaseModel):
    """Responder 生成的用户可见回答及其结构化数据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["complete", "partial", "unanswerable"]
    answer: str = Field(min_length=1)
    findings: tuple[dict[str, Any], ...] = ()
    limitations: tuple[dict[str, Any], ...] = ()
    table: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    audit: ResearchResponseAudit

    def answer_payload(self) -> dict[str, Any]:
        """返回可直接交给生命周期层的 JSON 载荷。"""

        return self.model_dump(mode="json")


class ResearchResponderInput(BaseModel):
    """Responder 的严格输入，只允许已提交的研究事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion: Completion
    findings: tuple[Finding, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    limitations: tuple[CompletionLimitation, ...] = ()
    chart_spec: dict[str, Any] | None = None
    completion_accepted: bool = True


class ResearchResponder:
    """根据已接受的 Completion 生成不含研究副作用的最终回答。"""

    def respond(
        self,
        source: ResearchResponderInput | None = None,
        *,
        completion: Completion | None = None,
        findings: Sequence[Finding] = (),
        evidences: Sequence[Evidence] = (),
        evidence: Sequence[Evidence] | None = None,
        limitations: Sequence[CompletionLimitation] = (),
        chart_spec: Mapping[str, Any] | None = None,
        completion_accepted: bool = True,
    ) -> ResearchResponse:
        """消费已确认事实并生成回答；不执行任何 Research 工具。"""

        if source is not None:
            if any(
                value is not None
                for value in (completion, evidence, chart_spec)
            ) or findings or limitations:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_INPUT_CONFLICT",
                    "不能同时传入结构化输入对象和分散参数",
                )
            completion = source.completion
            findings = source.findings
            evidences = source.evidence
            limitations = source.limitations
            chart_spec = source.chart_spec
            completion_accepted = source.completion_accepted
        elif completion is None:
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_COMPLETION_REQUIRED",
                "Responder 缺少 Completion",
            )

        if not completion_accepted:
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_COMPLETION_NOT_ACCEPTED",
                "只有通过 finish_research 校验的 Completion 才能生成回答",
            )
        if evidence is not None:
            if evidences:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_EVIDENCE_INPUT_CONFLICT",
                    "evidence 和 evidences 不能同时传入",
                )
            evidences = evidence

        assert completion is not None
        evidence_by_id = self._index_evidence(evidences)
        finding_by_id = self._index_findings(findings)
        selected_findings = self._select_findings(completion, finding_by_id)
        used_evidence_ids = self._used_evidence_ids(
            completion,
            selected_findings,
            evidence_by_id,
        )
        selected_evidence = tuple(evidence_by_id[item] for item in used_evidence_ids)

        self._validate_numbers(completion.summary, selected_evidence, "Completion")
        for finding in selected_findings:
            self._validate_numbers(
                finding.statement,
                selected_evidence,
                f"Finding {finding.finding_id}",
            )

        response_limitations = self._collect_limitations(
            completion,
            selected_evidence,
            limitations,
        )
        if completion.status in {"partial", "unanswerable"} and not response_limitations:
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_LIMITATION_REQUIRED",
                "partial 或 unanswerable 回答必须完整展示限制",
        )

        table = self._build_table(selected_evidence)
        chart, chart_sources = self._build_chart(
            chart_spec,
            {item.evidence_id: item for item in selected_evidence},
        )
        answer = self._build_answer(
            completion,
            selected_findings,
            response_limitations,
        )
        audit = ResearchResponseAudit(
            finding_ids=tuple(item.finding_id for item in selected_findings),
            evidence_ids=used_evidence_ids,
            chart_data_sources=chart_sources,
            final_status=completion.status,
        )
        return ResearchResponse(
            status=completion.status,
            answer=answer,
            findings=tuple(
                {
                    "finding_id": item.finding_id,
                    "statement": item.statement,
                    "evidence_ids": list(item.evidence_ids),
                }
                for item in selected_findings
            ),
            limitations=response_limitations,
            table=table,
            chart=chart,
            audit=audit,
        )

    @staticmethod
    def _index_evidence(
        evidences: Sequence[Evidence],
    ) -> dict[str, Evidence]:
        result: dict[str, Evidence] = {}
        for item in evidences:
            if item.evidence_id in result:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_EVIDENCE_DUPLICATED",
                    f"Evidence {item.evidence_id} 重复",
                )
            result[item.evidence_id] = item
        return result

    @staticmethod
    def _index_findings(findings: Sequence[Finding]) -> dict[str, Finding]:
        result: dict[str, Finding] = {}
        for item in findings:
            if item.finding_id in result:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_FINDING_DUPLICATED",
                    f"Finding {item.finding_id} 重复",
                )
            result[item.finding_id] = item
        return result

    @staticmethod
    def _select_findings(
        completion: Completion,
        finding_by_id: Mapping[str, Finding],
    ) -> tuple[Finding, ...]:
        selected: list[Finding] = []
        for finding_id in completion.finding_ids:
            finding = finding_by_id.get(finding_id)
            if finding is None:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_FINDING_NOT_FOUND",
                    f"Completion 引用了不存在的 Finding {finding_id}",
                )
            if finding.status != "confirmed":
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_FINDING_NOT_CONFIRMED",
                    f"Finding {finding_id} 已被替代，不能进入回答",
                )
            selected.append(finding)
        return tuple(selected)

    @staticmethod
    def _used_evidence_ids(
        completion: Completion,
        findings: Sequence[Finding],
        evidence_by_id: Mapping[str, Evidence],
    ) -> tuple[str, ...]:
        ordered: list[str] = []
        for evidence_id in (
            *completion.evidence_ids,
            *(evidence_id for finding in findings for evidence_id in finding.evidence_ids),
        ):
            if evidence_id not in evidence_by_id:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_EVIDENCE_NOT_FOUND",
                    f"回答引用的 Evidence {evidence_id} 不存在",
                )
            if evidence_id not in ordered:
                ordered.append(evidence_id)
        completion_evidence_ids = set(completion.evidence_ids)
        for finding in findings:
            missing = set(finding.evidence_ids) - completion_evidence_ids
            if missing:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_FINDING_EVIDENCE_NOT_INCLUDED",
                    f"Finding {finding.finding_id} 的证据未全部列入 Completion",
                )
        if completion.status == "complete" and not ordered:
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_EVIDENCE_REQUIRED",
                "complete 回答必须包含 Evidence",
            )
        return tuple(ordered)

    @staticmethod
    def _collect_limitations(
        completion: Completion,
        evidences: Sequence[Evidence],
        extra: Sequence[CompletionLimitation],
    ) -> tuple[dict[str, Any], ...]:
        items: list[dict[str, Any]] = []
        for completion_limitation in completion.limitations:
            items.append(completion_limitation.model_dump(mode="json"))
        for extra_limitation in extra:
            items.append(extra_limitation.model_dump(mode="json"))
        for evidence in evidences:
            for evidence_limitation in evidence.limitations:
                items.append(
                    {
                        "code": evidence_limitation.code,
                        "description": evidence_limitation.description,
                        "impact": evidence_limitation.impact,
                        "source_evidence_id": evidence.evidence_id,
                    }
                )
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for output_item in items:
            key = repr(sorted(output_item.items()))
            if key not in seen:
                seen.add(key)
                unique.append(output_item)
        return tuple(unique)

    @staticmethod
    def _build_answer(
        completion: Completion,
        findings: Sequence[Finding],
        limitations: Sequence[dict[str, Any]],
    ) -> str:
        lines = [completion.summary]
        if findings:
            lines.append("\n确认结论：")
            lines.extend(f"- {item.statement}" for item in findings)
        if limitations:
            lines.append("\n限制：")
            lines.extend(
                f"- {item['description']}（影响：{item['impact']}）"
                for item in limitations
            )
        return "\n".join(lines)

    @staticmethod
    def _build_table(evidences: Sequence[Evidence]) -> dict[str, Any] | None:
        for evidence in evidences:
            if not evidence.data.rows:
                continue
            return {
                "evidence_id": evidence.evidence_id,
                "columns": [item.name for item in evidence.columns],
                "data": [
                    dict(zip((item.name for item in evidence.columns), row, strict=True))
                    for row in evidence.data.rows
                ],
                "truncated": evidence.data.truncated,
            }
        return None

    def _build_chart(
        self,
        chart_spec: Mapping[str, Any] | None,
        evidence_by_id: Mapping[str, Evidence],
    ) -> tuple[dict[str, Any] | None, tuple[ResearchChartDataSource, ...]]:
        if chart_spec is None:
            return None, ()
        if not isinstance(chart_spec, Mapping):
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_CHART_SPEC_INVALID",
                "图表配置必须是对象",
            )
        if "data" in chart_spec:
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_CHART_DATA_FORBIDDEN",
                "图表数据必须由 Evidence 提供",
            )
        evidence_id = chart_spec.get("evidence_id") or chart_spec.get(
            "source_evidence_id"
        )
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_CHART_EVIDENCE_REQUIRED",
                "图表必须指定 Evidence",
            )
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_CHART_EVIDENCE_NOT_FOUND",
                f"图表引用的 Evidence {evidence_id} 不存在",
            )
        raw_columns = chart_spec.get("column_refs", chart_spec.get("columns"))
        if raw_columns is None:
            requested = [
                item.semantic_ref or item.name for item in evidence.columns
            ]
        elif isinstance(raw_columns, Sequence) and not isinstance(
            raw_columns, (str, bytes, bytearray)
        ):
            requested = list(raw_columns)
        else:
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_CHART_COLUMNS_INVALID",
                "图表列必须是数组",
            )
        selected = self._resolve_chart_columns(evidence, requested)
        selected_indexes = [index for index, _item in selected]
        selected_columns = [item for _index, item in selected]
        names = [item.name for item in selected_columns]
        chart = {
            "type": str(chart_spec.get("type") or "table"),
            "evidence_id": evidence_id,
            "columns": names,
            "data": [
                [row[index] for index in selected_indexes]
                for row in evidence.data.rows
            ],
            "truncated": evidence.data.truncated,
        }
        source = ResearchChartDataSource(
            evidence_id=evidence_id,
            column_refs=tuple(
                item.semantic_ref or item.name for item in selected_columns
            ),
        )
        return chart, (source,)

    @staticmethod
    def _resolve_chart_columns(
        evidence: Evidence,
        requested: Sequence[Any],
    ) -> list[tuple[int, Any]]:
        if not requested:
            raise ResearchResponderError(
                "RESEARCH_RESPONDER_CHART_COLUMNS_REQUIRED",
                "图表至少需要一列",
            )
        resolved: list[tuple[int, Any]] = []
        for ref in requested:
            if not isinstance(ref, str) or not ref:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_CHART_COLUMN_INVALID",
                    "图表列引用必须是非空字符串",
                )
            matches = [
                (index, column)
                for index, column in enumerate(evidence.columns)
                if column.semantic_ref == ref or column.name == ref
            ]
            if len(matches) != 1:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_CHART_COLUMN_NOT_FOUND",
                    f"图表列 {ref} 不存在或不能唯一定位到 Evidence 列",
                )
            if matches[0][0] in {index for index, _item in resolved}:
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_CHART_COLUMN_DUPLICATED",
                    f"图表列 {ref} 重复",
                )
            resolved.append(matches[0])
        return resolved

    @staticmethod
    def _validate_numbers(
        text: str,
        evidences: Sequence[Evidence],
        label: str,
    ) -> None:
        known = ResearchResponder._known_numbers(evidences)
        normalized = _DATE_PATTERN.sub(lambda match: " " * len(match.group()), text)
        normalized = _IDENTIFIER_PATTERN.sub(
            lambda match: " " * len(match.group()),
            normalized,
        )
        for match in _NUMBER_PATTERN.finditer(normalized):
            raw = text[match.start() : match.end()]
            value = ResearchResponder._decimal(raw.rstrip("%"))
            if value is None:
                continue
            if not ResearchResponder._matches_known_number(value, raw, known):
                raise ResearchResponderError(
                    "RESEARCH_RESPONDER_NUMBER_NOT_TRACEABLE",
                    f"{label} 中的数字 {raw} 无法追溯到 Evidence",
                )

    @staticmethod
    def _known_numbers(evidences: Sequence[Evidence]) -> set[Decimal]:
        # 证据条数是 Runtime 在预算收口摘要中使用的受控元数据，也纳入可追溯
        # 数字集合；业务数据仍然只从 Evidence 的行和统计值中读取。
        known: set[Decimal] = {Decimal(len(evidences))}
        for evidence in evidences:
            values: list[Any] = [evidence.data.row_count]
            values.extend(value for row in evidence.data.rows for value in row)
            values.extend(item.value for item in evidence.data.statistics)
            for value in values:
                if isinstance(value, bool) or value is None:
                    continue
                decimal = ResearchResponder._decimal(str(value))
                if decimal is not None:
                    known.add(decimal)
        return known

    @staticmethod
    def _decimal(value: str) -> Decimal | None:
        try:
            return Decimal(value.replace(",", ""))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _matches_known_number(
        value: Decimal,
        raw: str,
        known: set[Decimal],
    ) -> bool:
        numeric = raw.rstrip("%").replace(",", "")
        decimals = len(numeric.rsplit(".", 1)[1]) if "." in numeric else 0
        tolerance = Decimal("0.5") * (Decimal(10) ** -decimals)
        magnitude = abs(value)
        is_percent = raw.endswith("%")
        return any(
            abs(candidate - value) <= tolerance
            or abs(abs(candidate) - magnitude) <= tolerance
            or (
                is_percent
                and (
                    abs(candidate * 100 - value) <= tolerance
                    or abs(abs(candidate) * 100 - magnitude) <= tolerance
                )
            )
            for candidate in known
        )


def persist_research_response_audit(
    derived_state: Mapping[str, Any] | None,
    response: ResearchResponse,
) -> dict[str, Any]:
    """把回答审计信息写入派生状态，并保留其他研究事实。"""

    persisted = dict(derived_state or {})
    persisted[RESEARCH_RESPONSE_KEY] = response.audit.model_dump(mode="json")
    return persisted


__all__ = [
    "RESEARCH_RESPONSE_KEY",
    "ResearchChartDataSource",
    "ResearchResponder",
    "ResearchResponderError",
    "ResearchResponderInput",
    "ResearchResponse",
    "ResearchResponseAudit",
    "persist_research_response_audit",
]
