from apps.chat.models import chat_model
from common.utils.data_format import DataFormat
from common.utils.data_format_schema import AxisObj


def test_axis_obj_keeps_display_contract():
    axis = AxisObj(name="销售额", value="gmv", type="number")

    assert axis.model_dump(mode="json") == {
        "name": "销售额",
        "value": "gmv",
        "type": "number",
    }


def test_legacy_axis_obj_is_same_shared_schema():
    assert chat_model.AxisObj is AxisObj


def test_axis_obj_keeps_empty_defaults():
    assert AxisObj().model_dump(mode="json") == {
        "name": "",
        "value": "",
        "type": None,
    }


def test_data_format_consumes_public_axis_contract():
    rows, fields = DataFormat.convert_object_array_for_pandas(
        [AxisObj(name="城市", value="city"), AxisObj(name="销售额", value="gmv")],
        [{"city": "上海", "gmv": 100}],
    )

    assert fields == ["城市", "销售额"]
    assert rows == [["上海", 100]]
