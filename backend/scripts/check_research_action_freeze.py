"""检查旧 ResearchAction 是否在冻结期被扩展。"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).parent / "data" / "research_action_freeze_manifest.json"
RESEARCH_DTO = BACKEND_ROOT / "apps" / "chatbi" / "models" / "dto" / "research.py"


def _manifest() -> dict[str, list[str]]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _enum_members(tree: ast.AST) -> list[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ResearchActionType":
            names: list[str] = []
            for item in node.body:
                if (
                    isinstance(item, ast.Assign)
                    and len(item.targets) == 1
                    and isinstance(item.targets[0], ast.Name)
                ):
                    names.append(item.targets[0].id)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    names.append(item.target.id)
            return names
    raise RuntimeError("ResearchActionType 未找到")


def _action_models(tree: ast.AST) -> list[str]:
    return sorted(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name.startswith("Research")
        and node.name.endswith("Action")
    )


def check_freeze() -> list[str]:
    manifest = _manifest()
    tree = ast.parse(RESEARCH_DTO.read_text(encoding="utf-8"), filename=str(RESEARCH_DTO))
    errors: list[str] = []
    current_members = _enum_members(tree)
    expected_members = manifest["action_enum_members"]
    if current_members != expected_members:
        errors.append(
            f"ResearchActionType 已变化: expected={expected_members}, actual={current_members}"
        )
    current_models = _action_models(tree)
    expected_models = sorted(manifest["action_model_classes"])
    if current_models != expected_models:
        errors.append(
            f"ResearchAction DTO 已变化: expected={expected_models}, actual={current_models}"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="执行冻结检查")
    args = parser.parse_args()
    if not args.check:
        parser.error("必须显式传入 --check")
    errors = check_freeze()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("ResearchAction 冻结检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
