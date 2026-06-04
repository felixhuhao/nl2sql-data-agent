from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import Settings, get_settings

SCHEMA_PATH = PROJECT_ROOT / "docker" / "clickhouse" / "init.sql"
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
    parser = argparse.ArgumentParser(description="Create and seed the ClickHouse ecommerce dataset.")
    parser.add_argument("--input-dir", type=Path, default=PROJECT_ROOT / "data" / "clickhouse_csv")
    parser.add_argument("--schema-file", type=Path, default=SCHEMA_PATH)
    parser.add_argument("--skip-schema", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    result = seed_clickhouse(
        settings=settings,
        input_dir=args.input_dir,
        schema_file=args.schema_file,
        reset_schema=not args.skip_schema,
    )
    print(result)


def seed_clickhouse(
    settings: Settings,
    input_dir: Path,
    schema_file: Path = SCHEMA_PATH,
    reset_schema: bool = True,
) -> dict[str, int]:
    if reset_schema:
        for statement in _split_sql_statements(schema_file.read_text(encoding="utf-8")):
            _post_clickhouse(settings, statement)

    imported_counts: dict[str, int] = {}
    for table_name in TABLES:
        csv_path = input_dir / f"{table_name}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")
        query = f"INSERT INTO {_quote_identifier(settings.clickhouse_database)}.{_quote_identifier(table_name)} FORMAT CSVWithNames"
        _post_clickhouse(settings, query, data=csv_path.read_bytes())
        imported_counts[table_name] = _query_count(settings, table_name)
    return imported_counts


def _query_count(settings: Settings, table_name: str) -> int:
    query = f"SELECT count() FROM {_quote_identifier(settings.clickhouse_database)}.{_quote_identifier(table_name)}"
    raw = _post_clickhouse(settings, query)
    return int(raw.decode("utf-8").strip())


def _post_clickhouse(settings: Settings, query: str, data: bytes | None = None) -> bytes:
    params = {"user": settings.clickhouse_user}
    if settings.clickhouse_password:
        params["password"] = settings.clickhouse_password
    body = query.encode("utf-8") if data is None else data
    if data is not None:
        params["query"] = query

    request = Request(
        url=f"http://{settings.clickhouse_host}:{settings.clickhouse_port}/?{urlencode(params)}",
        data=body,
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.clickhouse_max_execution_time) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ClickHouse HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"ClickHouse HTTP request failed: {exc.reason}") from exc


def _split_sql_statements(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def _quote_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum() or identifier[0].isdigit():
        raise ValueError(f"Invalid ClickHouse identifier: {identifier}")
    return f"`{identifier}`"


if __name__ == "__main__":
    main()
