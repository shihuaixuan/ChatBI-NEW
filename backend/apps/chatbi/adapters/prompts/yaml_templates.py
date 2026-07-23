"""从后端模板目录读取 ChatBI 生成提示词。"""

from functools import cache
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from apps.datasource.database import DB

_BACKEND_DIR = Path(__file__).resolve().parents[4]
_BASE_TEMPLATE_PATH = _BACKEND_DIR / "templates" / "template.yaml"
_SQL_TEMPLATES_DIR = _BACKEND_DIR / "templates" / "sql_examples"


@cache
def _load_template_file(file_path: Path) -> dict[str, Any]:
    """读取并缓存一个 YAML 模板文件。"""
    try:
        with file_path.open(encoding="utf-8") as template_file:
            content = yaml.safe_load(template_file)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Template file not found at {file_path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Error parsing YAML file {file_path}: {exc}") from exc
    if not isinstance(content, dict):
        raise ValueError(f"Template file must contain a mapping: {file_path}")
    return content


def _base_template_section(name: str) -> dict[str, Any]:
    template = _load_template_file(_BASE_TEMPLATE_PATH)
    section = template["template"][name]
    if not isinstance(section, dict):
        raise ValueError(f"Template section must contain a mapping: {name}")
    return cast(dict[str, Any], section)


def get_analysis_template() -> dict[str, Any]:
    return _base_template_section("analysis")


def get_chart_template() -> dict[str, Any]:
    return _base_template_section("chart")


def get_datasource_template() -> dict[str, Any]:
    return _base_template_section("datasource")


def get_dynamic_template() -> dict[str, Any]:
    return _base_template_section("dynamic_sql")


def get_guess_question_template() -> dict[str, Any]:
    return _base_template_section("guess")


def get_permissions_template() -> dict[str, Any]:
    return _base_template_section("permissions")


def get_predict_template() -> dict[str, Any]:
    return _base_template_section("predict")


def get_sql_template() -> dict[str, Any]:
    return _base_template_section("sql")


def get_sql_example_template(db_type: str | DB) -> dict[str, Any]:
    if isinstance(db_type, str):
        db_enum = cast(
            DB,
            DB.get_db(db_type, default_if_none=True),  # type: ignore[no-untyped-call]
        )
    elif isinstance(db_type, DB):
        db_enum = db_type
    else:
        db_enum = DB.pg
    template = _load_template_file(
        _SQL_TEMPLATES_DIR / f"{db_enum.template_name}.yaml"
    )
    section = template["template"]
    if not isinstance(section, dict):
        raise ValueError("SQL example template must contain a mapping")
    return cast(dict[str, Any], section)
