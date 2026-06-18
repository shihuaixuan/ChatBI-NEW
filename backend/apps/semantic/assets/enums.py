from enum import Enum


class AssetType(str, Enum):
    DATASET = "DATASET"
    MODEL = "MODEL"
    METRIC = "METRIC"
    DIMENSION = "DIMENSION"
    DIMENSION_VALUE = "DIMENSION_VALUE"
    TERM = "TERM"
    EXAMPLE = "EXAMPLE"
    FIELD = "FIELD"
    DOCUMENT = "DOCUMENT"


class AssetStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    APPROVED = "APPROVED"
    DISABLED = "DISABLED"
    DEPRECATED = "DEPRECATED"


class RelationType(str, Enum):
    BELONGS_TO = "BELONGS_TO"
    USES_FIELD = "USES_FIELD"
    ANALYZABLE_BY = "ANALYZABLE_BY"
    HAS_VALUE = "HAS_VALUE"
    MAPS_TO = "MAPS_TO"
    EXAMPLE_OF = "EXAMPLE_OF"
    RELATED_TO = "RELATED_TO"


class AssetEventType(str, Enum):
    MetricChanged = "MetricChanged"
    DimensionChanged = "DimensionChanged"
    DimensionValueChanged = "DimensionValueChanged"
    TerminologyChanged = "TerminologyChanged"
    DataTrainingChanged = "DataTrainingChanged"
    DatasetChanged = "DatasetChanged"
    SchemaChanged = "SchemaChanged"

