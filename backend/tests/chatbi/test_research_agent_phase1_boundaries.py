from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.chatbi.models.dto.agent import AgentConfig
from apps.chatbi.models.dto.research_agent import ResearchExecutionMode
from apps.chatbi.orchestration.agent.run_orchestrator import (
    ensure_research_execution_mode_ready,
)
from apps.chatbi.orchestration.pipeline.mode_router import ModeRoutingError
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


def test_new_module_dependency_guard_passes() -> None:
    guard = _load_dependency_guard()
    assert guard.check_dependencies() == []


def test_research_execution_mode_defaults_to_legacy() -> None:
    assert Settings().CHATBI_RESEARCH_EXECUTION_MODE == "legacy"
    assert AgentConfig().research_execution_mode is ResearchExecutionMode.LEGACY


def test_research_execution_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        Settings(CHATBI_RESEARCH_EXECUTION_MODE="unknown")
    with pytest.raises(ValidationError):
        AgentConfig(research_execution_mode="unknown")


def test_research_execution_mode_accepts_explicit_shadow_and_agent() -> None:
    assert (
        Settings(CHATBI_RESEARCH_EXECUTION_MODE="shadow").CHATBI_RESEARCH_EXECUTION_MODE
        == "shadow"
    )
    assert (
        AgentConfig(research_execution_mode="agent").research_execution_mode
        is ResearchExecutionMode.AGENT
    )


def test_production_route_boundary_accepts_all_three_modes() -> None:
    assert (
        ensure_research_execution_mode_ready("research", "legacy")
        is ResearchExecutionMode.LEGACY
    )
    # 阶段 7 起 shadow 在路由入口合法：用户可见路径仍是 legacy，是否附带
    # 后台双跑由切流策略决定（doc38 §11.3.6）。
    assert (
        ensure_research_execution_mode_ready("research", "shadow")
        is ResearchExecutionMode.SHADOW
    )
    # 阶段 7.5 起 agent 同样在路由入口合法：主路径由新契约 Harness 执行，
    # 是否生效由切流策略裁决（配置即全量，不参与采样）。
    assert (
        ensure_research_execution_mode_ready("research", "agent")
        is ResearchExecutionMode.AGENT
    )
    assert ensure_research_execution_mode_ready("fast", "agent") is ResearchExecutionMode.AGENT
    assert ensure_research_execution_mode_ready("plan", "shadow") is ResearchExecutionMode.SHADOW
