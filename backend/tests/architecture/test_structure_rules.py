"""表驱动的架构结构守卫（R0 批次建立）。

规则即数据：新增结构规则 = 在下方规则表追加一行；禁止再按批次/按能力新增守卫测试文件
（见 apps/AGENTS.md v2 §9）。依赖基线棘轮仍由 test_dependency_baseline.py 负责，
本文件负责"当前必须为零违规"的结构性规则。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ForbiddenImportRule:
    """scope 内的模块禁止导入 forbidden 前缀（模块边界对齐，允许 allowed 前缀豁免）。"""

    rule_id: str
    scope: str
    forbidden: tuple[str, ...]
    allowed: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""


FORBIDDEN_IMPORT_RULES: tuple[ForbiddenImportRule, ...] = (
    ForbiddenImportRule(
        rule_id="chatbi-no-legacy-executor-imports",
        scope="apps/chatbi",
        forbidden=("apps.agent", "apps.workflow", "apps.chat"),
        reason="Agent 与 Graph 已归入 ChatBI，禁止恢复旧顶级执行器路径。",
    ),
    ForbiddenImportRule(
        rule_id="chatbi-core-no-orchestration-imports",
        scope="apps/chatbi/services",
        forbidden=("apps.chatbi.orchestration",),
        reason="ChatBI 核心能力不得反向依赖 Agent 或 Graph 编排。",
    ),
    ForbiddenImportRule(
        rule_id="tool-runtime-no-business-imports",
        scope="apps/tool",
        forbidden=(
            "apps.chatbi",
            "apps.semantic",
            "apps.knowledge",
            "apps.datasource",
            "apps.conversation",
            "apps.access_control",
            "apps.retrieval",
            "apps.assistant",
            "apps.dashboard",
            "apps.ai_model",
            "apps.system",
            "apps.platform_config",
            "apps.event",
            "apps.trace",
        ),
        reason="apps.tool 只承载通用工具运行时，不得依赖业务域、产品事件或 Trace。",
    ),
    ForbiddenImportRule(
        rule_id="datasource-services-no-concrete-tools",
        scope="apps/datasource/services",
        forbidden=(
            "apps.tool.tools",
            "apps.chatbi.orchestration.agent.tools",
        ),
        reason="Datasource 领域服务不得反向调用公共 Tool 或 ChatBI Tool。",
    ),
    ForbiddenImportRule(
        rule_id="semantic-services-no-concrete-tools",
        scope="apps/semantic/services",
        forbidden=(
            "apps.tool.tools",
            "apps.chatbi.orchestration.agent.tools",
        ),
        reason="Semantic 领域服务不得反向调用公共 Tool 或 ChatBI Tool。",
    ),
    ForbiddenImportRule(
        rule_id="knowledge-services-no-concrete-tools",
        scope="apps/knowledge/services",
        forbidden=(
            "apps.tool.tools",
            "apps.chatbi.orchestration.agent.tools",
        ),
        reason="Knowledge 领域服务不得反向调用公共 Tool 或 ChatBI Tool。",
    ),
    ForbiddenImportRule(
        rule_id="chatbi-tools-no-host-lifecycle-imports",
        scope="apps/chatbi/orchestration/agent/tools",
        forbidden=(
            "apps.chatbi.orchestration.agent.lifecycle",
            "apps.chatbi.orchestration.agent.tool_execution",
            "apps.chatbi.repository",
            "apps.event",
            "apps.trace",
        ),
        reason="ChatBI Tool 只做能力适配，不得直接处理宿主生命周期、持久化、Event 或 Trace。",
    ),
    ForbiddenImportRule(
        rule_id="engine-domain-no-business-imports",
        scope="platform/workflow_engine",
        forbidden=("apps",),
        reason="通用 Workflow Engine 的所有分层均不得依赖业务应用。",
    ),
    ForbiddenImportRule(
        rule_id="engine-tests-no-business-imports",
        scope="tests/workflow_engine",
        forbidden=("apps",),
        reason="Workflow Engine 测试环境只能依赖通用平台和公共基础设施。",
    ),
    ForbiddenImportRule(
        rule_id="mcp-no-chat-internals",
        scope="interfaces/mcp",
        forbidden=(
            "apps.chat.models",
            "apps.chat.api",
            "apps.chat.curd",
            "apps.chat.task",
        ),
        reason="MCP 只能使用 ChatBI 公开契约，不得重新依赖旧 Chat 内部路径。",
    ),
    ForbiddenImportRule(
        rule_id="dashboard-no-chat-internals",
        scope="apps/dashboard",
        forbidden=(
            "apps.chat.models",
            "apps.chat.api",
            "apps.chat.curd",
            "apps.chat.task",
        ),
        reason="Dashboard 只能通过 ChatBI 公开契约加载图表数据。",
    ),
    ForbiddenImportRule(
        rule_id="conversation-no-application-dependencies",
        scope="apps/conversation",
        forbidden=(
            "apps.chatbi",
            "apps.agent",
            "apps.workflow",
            "sqlbot_platform.workflow_engine",
        ),
        reason="Conversation 只拥有会话数据，不得依赖问数编排或工作流平台。",
    ),
    ForbiddenImportRule(
        rule_id="chatbi-no-conversation-internals",
        scope="apps/chatbi",
        forbidden=(
            "apps.conversation.models",
            "apps.conversation.repository",
            "apps.conversation.services",
            "apps.conversation.models.orm",
            "apps.conversation.repository.sqlmodel",
        ),
        reason="ChatBI 只能通过 Conversation Service 和公开 DTO 访问会话数据。",
    ),
)

FORBIDDEN_TOP_LEVEL_PATHS: tuple[str, ...] = (
    "apps/agent",
    "apps/capabilities",
    "apps/template",
    "apps/workflow",
    "apps/workflow_engine",
    "apps/mcp",
    "apps/settings",
)


def _module_matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _iter_absolute_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module


def _find_violations(rule: ForbiddenImportRule) -> list[str]:
    scope_dir = BACKEND_ROOT / rule.scope
    assert scope_dir.is_dir(), f"规则 {rule.rule_id} 的 scope 不存在: {rule.scope}"
    violations: list[str] = []
    for py_file in sorted(scope_dir.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        for lineno, module in _iter_absolute_imports(py_file):
            if any(_module_matches(module, allow) for allow in rule.allowed):
                continue
            if any(_module_matches(module, bad) for bad in rule.forbidden):
                rel = py_file.relative_to(BACKEND_ROOT)
                violations.append(f"{rel}:{lineno} -> {module}")
    return violations


@pytest.mark.parametrize(
    "rule", FORBIDDEN_IMPORT_RULES, ids=[r.rule_id for r in FORBIDDEN_IMPORT_RULES]
)
def test_forbidden_imports(rule: ForbiddenImportRule) -> None:
    violations = _find_violations(rule)
    assert not violations, (
        f"结构规则 {rule.rule_id} 违规（{rule.reason}）:\n" + "\n".join(violations)
    )


@pytest.mark.parametrize("relative_path", FORBIDDEN_TOP_LEVEL_PATHS)
def test_removed_chatbi_top_level_path_does_not_return(
    relative_path: str,
) -> None:
    """已归入 ChatBI 的顶级目录不得重新建立。"""

    assert not (BACKEND_ROOT / relative_path).exists()
