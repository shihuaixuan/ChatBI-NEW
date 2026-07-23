"""ChatBI 生成适配器使用的提示词模板。"""

from apps.chatbi.adapters.prompts.yaml_templates import (
    get_analysis_template,
    get_chart_template,
    get_datasource_template,
    get_dynamic_template,
    get_guess_question_template,
    get_permissions_template,
    get_predict_template,
    get_sql_example_template,
    get_sql_template,
)

__all__ = [
    "get_analysis_template",
    "get_chart_template",
    "get_datasource_template",
    "get_dynamic_template",
    "get_guess_question_template",
    "get_permissions_template",
    "get_predict_template",
    "get_sql_example_template",
    "get_sql_template",
]
