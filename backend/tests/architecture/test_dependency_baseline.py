"""DDD 迁移期的全局依赖基线守卫。"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
APPS_DIR = BACKEND_DIR / "apps"
WORKFLOW_ENGINE_DIR = BACKEND_DIR / "platform" / "workflow_engine"
BASELINE_PATH = Path(__file__).with_name("known_dependency_violations.json")

# 旧模块尚未统一 models/orm，迁移期通过这些前缀识别其内部模型。
LEGACY_INTERNAL_MODEL_PREFIXES = (
    "apps.chat.models",
    "apps.datasource.models",
    "apps.semantic.models.orm",
    "apps.system.models",
    "sqlbot_platform.workflow_engine.infrastructure.persistence.models",
)

# 该模块仅为当前发布版 xpack 的固定导入路径保留，删除条件登记在兼容台账 A2。
EXTERNAL_COMPATIBILITY_PATHS = frozenset(
    {"apps/data_training/models/data_training_model.py"}
)

# 这些路径表示具体实现，而不是可供跨领域依赖的公开契约。
CONCRETE_IMPLEMENTATION_MARKERS = (
    ".crud",
    ".curd",
    ".repository",
    ".infrastructure",
    ".task",
)

RULE_NAMES = (
    "cross_domain_internal_models",
    "cross_domain_concrete_implementations",
    "cross_domain_api_imports",
    "workflow_engine_business_imports",
    "function_local_app_imports",
)


@dataclass(frozen=True, slots=True)
class ImportRecord:
    """单条 Python import 记录。"""

    path: str
    source_app: str
    module: str
    line: int
    function_local: bool

    @property
    def target_app(self) -> str | None:
        parts = self.module.split(".")
        if len(parts) < 2 or parts[0] != "apps":
            return None
        return parts[1]

    @property
    def key(self) -> str:
        return f"{self.path} -> {self.module}"


class _ImportVisitor(ast.NodeVisitor):
    """收集 import，并记录它是否位于函数内部。"""

    def __init__(self, path: str, source_app: str) -> None:
        self._path = path
        self._source_app = source_app
        self._function_depth = 0
        self.records: list[ImportRecord] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._append(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._append(node.module, node.lineno)

    def _append(self, module: str, line: int) -> None:
        self.records.append(
            ImportRecord(
                path=self._path,
                source_app=self._source_app,
                module=module,
                line=line,
                function_local=self._function_depth > 0,
            )
        )


def _iter_imports() -> list[ImportRecord]:
    records: list[ImportRecord] = []
    source_files = [
        *((path, path.relative_to(APPS_DIR).parts[0]) for path in APPS_DIR.rglob("*.py")),
        *((path, "workflow_engine") for path in WORKFLOW_ENGINE_DIR.rglob("*.py")),
    ]
    for path, source_app in sorted(source_files):
        if "__pycache__" in path.parts:
            continue
        relative_path = path.relative_to(BACKEND_DIR)
        visitor = _ImportVisitor(str(relative_path), source_app)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        records.extend(visitor.records)
    return records


def _collect_violations() -> dict[str, set[str]]:
    violations = {rule_name: set() for rule_name in RULE_NAMES}
    for record in _iter_imports():
        if record.path in EXTERNAL_COMPATIBILITY_PATHS:
            continue
        target_app = record.target_app
        is_cross_domain = target_app is not None and target_app != record.source_app

        is_internal_model = ".models.orm" in record.module or record.module.startswith(
            LEGACY_INTERNAL_MODEL_PREFIXES
        )
        if is_cross_domain and is_internal_model:
            violations["cross_domain_internal_models"].add(record.key)

        if is_cross_domain and any(
            marker in record.module for marker in CONCRETE_IMPLEMENTATION_MARKERS
        ):
            violations["cross_domain_concrete_implementations"].add(record.key)

        module_parts = record.module.split(".")
        is_cross_domain_api = (
            is_cross_domain
            and len(module_parts) >= 3
            and module_parts[2] == "api"
            and record.path != "apps/api.py"
        )
        if is_cross_domain_api:
            violations["cross_domain_api_imports"].add(record.key)

        if record.source_app == "workflow_engine" and is_cross_domain:
            violations["workflow_engine_business_imports"].add(record.key)

        if record.function_local and record.module.startswith("apps."):
            violations["function_local_app_imports"].add(record.key)

    return violations


def _load_baseline() -> dict[str, list[str]]:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert set(payload) == set(RULE_NAMES), "依赖基线中的规则名称与测试不一致"
    for rule_name, entries in payload.items():
        assert entries == sorted(set(entries)), f"{rule_name} 的基线必须去重并排序"
    return payload


@pytest.mark.parametrize("rule_name", RULE_NAMES)
def test_dependency_violations_match_explicit_baseline(rule_name: str) -> None:
    """禁止新增违规依赖，并要求已清理项同步移出基线。"""

    baseline = set(_load_baseline()[rule_name])
    actual = _collect_violations()[rule_name]
    added = sorted(actual - baseline)
    removed = sorted(baseline - actual)

    assert not added, (
        f"{rule_name} 出现新的违规依赖，请改用公开 Service、DTO 或端口：\n"
        + "\n".join(added)
    )
    assert not removed, (
        f"{rule_name} 已清理以下历史依赖，请同步更新基线：\n"
        + "\n".join(removed)
    )
