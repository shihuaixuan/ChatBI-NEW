import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_project_has_no_xpack_python_imports_or_dependency():
    imported_modules: list[str] = []
    for path in BACKEND_ROOT.rglob("*.py"):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

    assert not any(
        module == "sqlbot_xpack" or module.startswith("sqlbot_xpack.")
        for module in imported_modules
    )
    assert "sqlbot-xpack" not in (
        BACKEND_ROOT / "pyproject.toml"
    ).read_text(encoding="utf-8")


def test_application_has_no_xpack_routes_or_static_mount():
    import main

    paths = {getattr(route, "path", "") for route in main.app.routes}
    normalized_paths = {
        path.removeprefix("/api/v1")
        if path.startswith("/api/v1")
        else path
        for path in paths
    }

    assert "/xpack_static" not in normalized_paths
    assert not any(
        path.startswith(("/license", "/system/license"))
        for path in normalized_paths
    )
    assert not any(
        path.startswith("/system/authentication")
        for path in normalized_paths
    )
    assert not any(
        path.startswith(("/system/custom-prompt", "/system/custom_prompt"))
        for path in normalized_paths
    )
    assert "/system/appearance/ui" in normalized_paths
    assert "/system/embedded/{page_num}/{page_size}" in normalized_paths
    assert "/ds_permission/list" in normalized_paths
