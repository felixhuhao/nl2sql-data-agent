from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RawResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int


@dataclass(frozen=True)
class ColumnMeta:
    name: str
    data_type: str
    nullable: bool
    sample_values: list[str] = field(default_factory=list)
    is_partition_key: bool = False
    is_sorting_key: bool = False
    is_primary_key: bool = False
    low_cardinality: bool = False


@dataclass(frozen=True)
class TableMeta:
    name: str
    row_count: int
    columns: list[ColumnMeta]
    engine: str = ""
    partition_key: str = ""
    sorting_key: str = ""


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: list[TableMeta]
    datasource_name: str
    synced_at: datetime


@dataclass(frozen=True)
class DataSourceInfo:
    name: str
    dialect: str
    display_name: str
    status: str = "available"
