"""阶段 8 删除边界：旧 ResearchAction 架构与切流配置不得回流。"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from apps.chatbi.models.dto.agent import AgentConfig
from common.core.config import Settings


def _load_dependency_guard():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "check_research_agent_dependencies.py"
    )
    spec = importlib.util.spec_from_file_location("research_agent_dependency_guard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 Research Agent 依赖守卫")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_removal_boundary_dependency_guard_passes() -> None:
    """全仓反向断言：已删除模块无任何残留引用，新契约模块齐全。"""

    guard = _load_dependency_guard()
    assert guard.check_dependencies() == []


@pytest.mark.parametrize(
    "module_name",
    [
        "apps.chatbi.models.dto.research",
        "apps.chatbi.services.research.actions",
        "apps.chatbi.services.research.action_batches",
        "apps.chatbi.services.research.policy",
        "apps.chatbi.services.research.policy_rules",
        "apps.chatbi.services.research.hypotheses",
        "apps.chatbi.services.research.evidence",
        "apps.chatbi.services.research.ports",
        "apps.chatbi.services.research.report",
        "apps.chatbi.services.research.shadow",
        "apps.chatbi.services.research.rollout",
        "apps.chatbi.services.research.requirements",
        "apps.chatbi.orchestration.pipeline.research",
        "apps.chatbi.adapters.prompts.research_policy",
    ],
)
def test_deleted_modules_are_gone(module_name: str) -> None:
    """旧架构模块必须物理不存在（可导入即回归）。"""

    assert module_name not in sys.modules
    with pytest.raises(ModuleNotFoundError):
        __import__(module_name)


def test_removed_config_fields_do_not_exist() -> None:
    """三态开关、shadow 采样/白名单与 per-iteration Action 预算随架构删除。"""

    settings_fields = set(Settings.model_fields)
    for name in (
        "CHATBI_RESEARCH_EXECUTION_MODE",
        "CHATBI_RESEARCH_SHADOW_SAMPLE_RATE",
        "CHATBI_RESEARCH_SHADOW_DATASET_ALLOWLIST",
        "CHATBI_RESEARCH_SHADOW_TENANT_ALLOWLIST",
        "CHAT_AGENT_RESEARCH_MAX_ACTIONS_PER_ITERATION",
    ):
        assert name not in settings_fields, name
    config_fields = set(AgentConfig.model_fields)
    for name in (
        "research_execution_mode",
        "research_shadow_sample_rate",
        "research_shadow_dataset_allowlist",
        "research_shadow_tenant_allowlist",
        "research_max_actions_per_iteration",
    ):
        assert name not in config_fields, name


def test_deprecated_research_environment_is_rejected() -> None:
    """旧开关残留必须让进程显式失败，不能被配置层静默忽略。"""

    deprecated_names = (
        "CHATBI_RESEARCH_EXECUTION_MODE",
        "CHATBI_RESEARCH_SHADOW_SAMPLE_RATE",
        "CHATBI_RESEARCH_SHADOW_DATASET_ALLOWLIST",
        "CHATBI_RESEARCH_SHADOW_TENANT_ALLOWLIST",
        "CHAT_AGENT_RESEARCH_MAX_ACTIONS_PER_ITERATION",
    )
    environment = os.environ.copy()
    for name in deprecated_names:
        environment.pop(name, None)
    environment["CHATBI_RESEARCH_EXECUTION_MODE"] = "legacy"
    result = subprocess.run(
        [sys.executable, "-c", "import common.core.config"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "DEPRECATED_RESEARCH_ENVIRONMENT_VARIABLES:" in result.stderr
    assert "CHATBI_RESEARCH_EXECUTION_MODE" in result.stderr


def test_execution_route_has_no_origin_field() -> None:
    """路由决策只保留 mode + reasons；legacy/shadow/agent 三态 origin 已删。"""

    from apps.chatbi.models.dto.execution_requirement import ExecutionRoute

    assert set(ExecutionRoute.model_fields) == {"mode", "reasons"}
