from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import get_settings

TABLES = [
    "dim_date",
    "dim_users",
    "dim_products",
    "dim_regions",
    "dim_channels",
    "fact_orders",
    "fact_order_items",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the DuckDB ecommerce dataset to CSVWithNames files.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "clickhouse_csv")
    parser.add_argument("--duckdb-path", type=Path, default=None)
    args = parser.parse_args()

    duckdb_path = args.duckdb_path or get_settings().resolved_duckdb_path()
    counts = export_tables(duckdb_path=duckdb_path, output_dir=args.output_dir)
    for table_name, row_count in counts.items():
        print(f"{table_name}: {row_count} rows")


def export_tables(duckdb_path: Path, output_dir: Path, tables: list[str] | None = None) -> dict[str, int]:
    if not duckdb_path.exists():
        raise FileNotFoundError(f"DuckDB file not found: {duckdb_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    table_names = tables or TABLES
    counts: dict[str, int] = {}
    with duckdb.connect(str(duckdb_path), read_only=True) as connection:
        for table_name in table_names:
            csv_path = output_dir / f"{table_name}.csv"
            connection.execute(
                f"""
                COPY (SELECT * FROM {_quote_identifier(table_name)})
                TO '{_quote_path(csv_path)}'
                (HEADER, DELIMITER ',')
                """
            )
            row = connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}"
            ).fetchone()
            if row is None:
                raise RuntimeError(f"COUNT(*) returned no row for {table_name}.")
            counts[table_name] = row[0]
    return counts


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


if __name__ == "__main__":
    main()
