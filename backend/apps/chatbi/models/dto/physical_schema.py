from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PhysicalSchemaField:
    name: str
    data_type: str | None
    comment: str


@dataclass(frozen=True, slots=True)
class PhysicalSchemaTable:
    name: str
    comment: str
    fields: list[PhysicalSchemaField]


@dataclass(frozen=True, slots=True)
class PhysicalSchemaResult:
    tables: list[PhysicalSchemaTable]


__all__ = [
    "PhysicalSchemaField",
    "PhysicalSchemaResult",
    "PhysicalSchemaTable",
]
