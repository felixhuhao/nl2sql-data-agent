from datetime import datetime, timezone
import logging
import re
from typing import Any

from backend.app.config import Settings
from backend.app.connectors.schema import ColumnMeta, RawResult, SchemaSnapshot, TableMeta

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
logger = logging.getLogger(__name__)


class ClickHouseConnector:
    name = "clickhouse_ecommerce"
    dialect = "clickhouse"
    display_name = "ClickHouse (OLAP)"

    def __init__(self, settings: Settings):
        self._settings = settings
        self.database = settings.clickhouse_database

    def get_connection(self, read_only: bool = True) -> Any:
        clickhouse_connect = _load_clickhouse_connect()
        connection_settings: dict[str, int] = {
            "max_execution_time": self._settings.clickhouse_max_execution_time,
            "max_result_rows": self._settings.clickhouse_max_result_rows,
        }
        if read_only and self._settings.clickhouse_readonly:
            connection_settings["readonly"] = 1

        return clickhouse_connect.get_client(
            host=self._settings.clickhouse_host,
            port=self._settings.clickhouse_port,
            username=self._settings.clickhouse_user,
            password=self._settings.clickhouse_password,
            database=self.database,
            settings=connection_settings,
        )

    def ping(self) -> None:
        client = self.get_connection(read_only=True)
        try:
            client.query("SELECT 1")
        finally:
            _close_client(client)

    def sync_schema(self) -> SchemaSnapshot:
        client = self.get_connection(read_only=True)
        try:
            table_rows = _result_rows(
                client.query(
                    """
                    SELECT name, engine, total_rows, partition_key, sorting_key
                    FROM system.tables
                    WHERE database = {database:String}
                      AND engine NOT IN ('View', 'MaterializedView', 'SystemView')
                    ORDER BY name
                    """,
                    parameters={"database": self.database},
                )
            )
            column_rows = _result_rows(
                client.query(
                    """
                    SELECT table, name, type,
                           is_in_partition_key, is_in_sorting_key, is_in_primary_key
                    FROM system.columns
                    WHERE database = {database:String}
                    ORDER BY table, position
                    """,
                    parameters={"database": self.database},
                )
            )

            columns_by_table: dict[str, list[ColumnMeta]] = {}
            for table_name, column_name, data_type, partition_key, sorting_key, primary_key in column_rows:
                columns_by_table.setdefault(table_name, []).append(
                    ColumnMeta(
                        name=column_name,
                        data_type=_base_type(str(data_type)),
                        nullable=_is_nullable(str(data_type)),
                        sample_values=self._read_sample_values(client, table_name, column_name),
                        is_partition_key=bool(partition_key),
                        is_sorting_key=bool(sorting_key),
                        is_primary_key=bool(primary_key),
                        low_cardinality=_is_low_cardinality(str(data_type)),
                    )
                )

            tables = [
                TableMeta(
                    name=table_name,
                    row_count=int(total_rows or 0),
                    columns=columns_by_table.get(table_name, []),
                    engine=engine or "",
                    partition_key=partition_key or "",
                    sorting_key=sorting_key or "",
                )
                for table_name, engine, total_rows, partition_key, sorting_key in table_rows
            ]
        finally:
            _close_client(client)

        return SchemaSnapshot(
            tables=tables,
            datasource_name=self.name,
            synced_at=datetime.now(timezone.utc),
        )

    def execute(self, sql: str, timeout: int | None = None) -> RawResult:
        client = self.get_connection(read_only=True)
        try:
            return _query_raw(client, sql, timeout=timeout)
        finally:
            _close_client(client)

    def execute_with_explain(self, sql: str, timeout: int | None = None) -> tuple[RawResult, dict | None]:
        client = self.get_connection(read_only=True)
        try:
            result = _query_raw(client, sql, timeout=timeout)
            try:
                explain_result = _query_raw(client, f"EXPLAIN PIPELINE TREE {sql}")
            except Exception as exc:
                logger.warning("ClickHouse EXPLAIN failed: %s", exc)
                explain_result = None
            return result, _parse_explain_result(explain_result) if explain_result is not None else None
        finally:
            _close_client(client)

    def explain(self, sql: str) -> dict | None:
        client = self.get_connection(read_only=True)
        try:
            result = _query_raw(client, f"EXPLAIN PIPELINE TREE {sql}")
            return _parse_explain_result(result)
        finally:
            _close_client(client)

    def close(self) -> None:
        return None

    def _read_sample_values(self, client: Any, table_name: str, column_name: str, limit: int = 5) -> list[str]:
        query = (
            f"SELECT DISTINCT {_quote_identifier(column_name)} "
            f"FROM {_quote_identifier(self.database)}.{_quote_identifier(table_name)} "
            f"WHERE {_quote_identifier(column_name)} IS NOT NULL "
            f"ORDER BY 1 LIMIT {limit}"
        )
        rows = _result_rows(client.query(query))
        return [str(row[0]) for row in rows]


def _load_clickhouse_connect():
    try:
        import clickhouse_connect
    except ImportError as exc:
        raise RuntimeError("clickhouse-connect is required when CLICKHOUSE_ENABLED=true.") from exc
    return clickhouse_connect


def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _result_rows(result: Any) -> list:
    return list(getattr(result, "result_rows", None) or getattr(result, "result_set", None) or [])


def _query_raw(client: Any, sql: str, timeout: int | None = None) -> RawResult:
    query_settings = {"max_execution_time": timeout} if timeout is not None else None
    result = client.query(sql, settings=query_settings)
    rows = [list(row) for row in _result_rows(result)]
    columns = list(getattr(result, "column_names", []) or [])
    return RawResult(columns=columns, rows=rows, row_count=len(rows))


def _parse_explain_result(result: RawResult) -> dict | None:
    lines = [
        " ".join(str(cell) for cell in row if cell is not None).strip()
        for row in result.rows
    ]
    lines = [line for line in lines if line]
    if not lines:
        return None
    return {
        "format": "PIPELINE TREE",
        "lines": lines,
        "text": "\n".join(lines),
    }


def _quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"Invalid ClickHouse identifier: {identifier}")
    return f"`{identifier}`"


def _is_nullable(data_type: str) -> bool:
    return "Nullable(" in data_type


def _is_low_cardinality(data_type: str) -> bool:
    return "LowCardinality(" in data_type


def _base_type(data_type: str) -> str:
    current = data_type
    changed = True
    while changed:
        changed = False
        for wrapper in ("Nullable", "LowCardinality"):
            prefix = f"{wrapper}("
            if current.startswith(prefix) and current.endswith(")"):
                current = current[len(prefix) : -1]
                changed = True
    return current
