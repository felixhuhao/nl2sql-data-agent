from datetime import datetime, timezone
from typing import Any

import duckdb

from backend.app.config import Settings
from backend.app.connectors.schema import ColumnMeta, RawResult, SchemaSnapshot, TableMeta


class DuckDBConnector:
    name = "duckdb_ecommerce"
    dialect = "duckdb"
    display_name = "DuckDB (本地)"

    def __init__(self, settings: Settings):
        self._settings = settings

    def get_connection(self, read_only: bool = True) -> duckdb.DuckDBPyConnection:
        path = self._settings.resolved_duckdb_path()
        if read_only and not path.exists():
            raise FileNotFoundError(f"DuckDB file not found: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(path), read_only=read_only)

    def sync_schema(self) -> SchemaSnapshot:
        with self.get_connection(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'main'
                ORDER BY table_name, ordinal_position
                """
            ).fetchall()

            table_columns: dict[str, list[ColumnMeta]] = {}
            for table_name, column_name, data_type, is_nullable in rows:
                table_columns.setdefault(table_name, []).append(
                    ColumnMeta(
                        name=column_name,
                        data_type=data_type,
                        nullable=is_nullable == "YES",
                        sample_values=self._read_sample_values(connection, table_name, column_name),
                    )
                )

            tables = [
                TableMeta(
                    name=table_name,
                    row_count=self._count_rows(connection, table_name),
                    columns=columns,
                )
                for table_name, columns in table_columns.items()
            ]

        return SchemaSnapshot(
            tables=tables,
            datasource_name=self.name,
            synced_at=datetime.now(timezone.utc),
        )

    def execute(self, sql: str, timeout: int | None = None) -> RawResult:
        del timeout
        connection = self.get_connection(read_only=True)
        try:
            cursor = connection.execute(sql)
            rows = [list(row) for row in cursor.fetchall()]
            columns = [column[0] for column in cursor.description or []]
            return RawResult(columns=columns, rows=rows, row_count=len(rows))
        finally:
            connection.close()

    def explain(self, sql: str) -> dict | None:
        connection = self.get_connection(read_only=True)
        try:
            cursor = connection.execute(f"EXPLAIN {sql}")
            rows = cursor.fetchall()
        finally:
            connection.close()

        lines: list[str] = []
        for row in rows:
            if len(row) >= 2 and str(row[1]).strip():
                lines.extend(str(row[1]).splitlines())
            elif row and str(row[0]).strip():
                lines.append(str(row[0]))
        return {
            "dialect": self.dialect,
            "format": "text",
            "lines": [line for line in lines if line.strip()],
        }

    def close(self) -> None:
        return None

    @staticmethod
    def _count_rows(connection: duckdb.DuckDBPyConnection, table_name: str) -> int:
        return connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}").fetchone()[0]

    @staticmethod
    def _read_sample_values(
        connection: duckdb.DuckDBPyConnection,
        table_name: str,
        column_name: str,
        limit: int = 5,
    ) -> list[str]:
        rows = connection.execute(
            f"""
            SELECT DISTINCT {_quote_identifier(column_name)}
            FROM {_quote_identifier(table_name)}
            WHERE {_quote_identifier(column_name)} IS NOT NULL
            ORDER BY 1
            LIMIT {limit}
            """
        ).fetchall()
        return [str(row[0]) for row in rows]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
