"""XPack 旧模型路径兼容层，不再定义 SQL 示例模型。"""

from apps.knowledge.models.dto.sql_example import (
    SQLExampleInput,
    SQLExampleResult,
)
from apps.knowledge.models.orm.sql_example import SQLExampleModel

DataTraining = SQLExampleModel
DataTrainingInfo = SQLExampleInput
DataTrainingInfoResult = SQLExampleResult

__all__ = ["DataTraining", "DataTrainingInfo", "DataTrainingInfoResult"]
