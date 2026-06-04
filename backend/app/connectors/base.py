from typing import Any, Protocol

from backend.app.connectors.schema import RawResult, SchemaSnapshot


class DataSourceConnector(Protocol):
    name: str
    dialect: str
    display_name: str

    def get_connection(self, read_only: bool = True) -> Any:
        ...

    def sync_schema(self) -> SchemaSnapshot:
        ...

    def execute(self, sql: str, timeout: int | None = None) -> RawResult:
        ...

    def explain(self, sql: str) -> dict | None:
        ...

    def close(self) -> None:
        ...
