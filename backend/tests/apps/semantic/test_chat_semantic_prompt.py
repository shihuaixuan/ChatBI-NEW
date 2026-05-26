from apps.chat.models.chat_model import AiModelQuestion, OperationEnum


def test_operation_enum_contains_filter_semantic_asset():
    assert OperationEnum.FILTER_SEMANTIC_ASSET.value == "14"


def test_ai_model_question_semantic_context_default_preserves_prompt_keys():
    question = AiModelQuestion(engine="PostgreSQL", db_schema="orders(id bigint)")

    templates = question.sql_sys_question("PostgreSQL")

    assert question.semantic_context == ""
    assert "semantic_context" not in templates


def test_sql_sys_question_injects_semantic_context_after_schema_before_terms_and_examples():
    question = AiModelQuestion(
        engine="PostgreSQL",
        db_schema="orders(pay_amount decimal)",
        semantic_context="已审核业务指标:\n- 销售额 (sales_amount)；默认聚合: SUM；表达式: pay_amount",
        terminologies="术语内容",
        data_training="SQL 示例内容",
    )

    templates = question.sql_sys_question("PostgreSQL")
    template_keys = list(templates.keys())

    assert template_keys.index("schema") < template_keys.index("semantic_context")
    assert template_keys.index("semantic_context") < template_keys.index("terminologies")
    assert template_keys.index("semantic_context") < template_keys.index("data_training")
    assert "已审核业务指标" in templates["semantic_context"]
    assert "命中业务词时优先使用已审核指标维度口径" in templates["semantic_context"]
    assert "不自行改写聚合方式" in templates["semantic_context"]
