from apps.chatbi.adapters.analysis_prediction import (
    TemplateAnalysisPredictionPromptBuilder,
)
from apps.chatbi.models import (
    AnalysisPredictionGenerationData,
)
from apps.conversation import ChatRecordAuxiliaryType


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
        terminologies="销售额：已支付订单金额",
    )


def test_analysis_prompt_keeps_terms_fields_and_data():
    messages = TemplateAnalysisPredictionPromptBuilder().build(
        _data(ChatRecordAuxiliaryType.ANALYSIS)
    )

    assert [message.role for message in messages] == ["system", "human"]
    assert "销售额：已支付订单金额" in messages[0].content
    assert '["月份","销售额"]' in messages[1].content
    assert '[["1月",100]]' in messages[1].content


def test_prediction_prompt_keeps_fields_and_data():
    messages = TemplateAnalysisPredictionPromptBuilder().build(
        _data(ChatRecordAuxiliaryType.PREDICT)
    )

    assert [message.role for message in messages] == ["system", "human"]
    assert "进行数据预测" in messages[0].content
    assert '["月份","销售额"]' in messages[1].content
    assert '[["1月",100]]' in messages[1].content
