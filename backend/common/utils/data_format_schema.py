from pydantic import BaseModel


class AxisObj(BaseModel):
    """表格与图表展示使用的列定义。"""

    name: str = ""
    value: str = ""
    type: str | None = None


__all__ = ["AxisObj"]
