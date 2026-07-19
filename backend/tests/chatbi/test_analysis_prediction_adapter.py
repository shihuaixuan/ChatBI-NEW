from apps.chatbi.models import (
    AnalysisPredictionGenerationData,
    ChatRecordAuxiliaryType,
)
from infrastructure.analysis_prediction import (
    TemplateAnalysisPredictionPromptBuilder,
)


def _data(
    generation_type: ChatRecordAuxiliaryType,
) -> AnalysisPredictionGenerationData:
    return AnalysisPredictionGenerationData(
        record_id=10,
        generation_type=generation_type,
        fields='["月份","销售额"]',
        data='[["1月",100]]',
        language="简体中文",
        assistant_name="Numora",
        custom_prompt="重点关注异常变化",
        terminologies="销售额：已支付订单金额",
    )


def test_analysis_prompt_keeps_terms_custom_prompt_fields_and_data():
    messages = TemplateAnalysisPredictionPromptBuilder().build(
        _data(ChatRecordAuxiliaryType.ANALYSIS)
    )

    assert [message.role for message in messages] == ["system", "human"]
    assert "销售额：已支付订单金额" in messages[0].content
    assert "重点关注异常变化" in messages[0].content
    assert '["月份","销售额"]' in messages[1].content
    assert '[["1月",100]]' in messages[1].content


def test_prediction_prompt_keeps_custom_prompt_fields_and_data():
    messages = TemplateAnalysisPredictionPromptBuilder().build(
        _data(ChatRecordAuxiliaryType.PREDICT)
    )

    assert [message.role for message in messages] == ["system", "human"]
    assert "重点关注异常变化" in messages[0].content
    assert "进行数据预测" in messages[0].content
    assert '["月份","销售额"]' in messages[1].content
    assert '[["1月",100]]' in messages[1].content
