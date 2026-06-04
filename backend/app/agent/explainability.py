from __future__ import annotations

from datetime import datetime, timedelta

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from backend.app.config import get_settings
from backend.app.metadata.models import DEFAULT_DATASOURCE
from backend.app.metadata.service import list_relationships
from backend.app.sql_guard.models import GuardResult


def build_query_explainability(
    sql: str,
    question: str,
    guard_result: GuardResult,
    datasource_name: str = DEFAULT_DATASOURCE,
    datasource_dialect: str = "duckdb",
) -> dict:
    matched_tables, matched_columns = _extract_sql_assets(sql, dialect=datasource_dialect)
    return {
        "datasource": {
            "name": datasource_name,
            "dialect": datasource_dialect,
        },
        "matched_tables": matched_tables,
        "matched_columns": matched_columns,
        "join_paths": _matched_join_paths(matched_tables, datasource_name=datasource_name),
        "date_interpretation": _date_interpretation(question),
        "guard_result": guard_result.model_dump(),
    }


def _extract_sql_assets(sql: str, dialect: str = "duckdb") -> tuple[list[str], list[str]]:
    try:
        expression = sqlglot.parse_one(sql, read=dialect)
    except ParseError:
        return [], []

    aliases = _table_aliases(expression)
    tables = sorted(set(aliases.values()))
    columns = []
    for column in expression.find_all(exp.Column):
        column_name = column.name
        if column_name == "*":
            continue
        table_name = aliases.get(column.table)
        if table_name is None and len(tables) == 1:
            table_name = tables[0]
        columns.append(f"{table_name}.{column_name}" if table_name else column_name)
    return tables, sorted(set(columns))


def _table_aliases(expression: exp.Expression) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        table_name = table.name
        aliases[table_name] = table_name
        aliases[table.alias_or_name] = table_name
    return aliases


def _matched_join_paths(matched_tables: list[str], datasource_name: str = DEFAULT_DATASOURCE) -> list[dict]:
    matched_table_set = set(matched_tables)
    return [
        relationship
        for relationship in list_relationships(datasource_name=datasource_name)
        if relationship["source_table"] in matched_table_set
        and relationship["target_table"] in matched_table_set
    ]


def _date_interpretation(question: str) -> dict:
    end = get_settings().dataset_current_date
    if "最近30天" not in question:
        return {
            "matched": False,
            "dataset_current_date": end,
        }
    start = (datetime.strptime(end, "%Y-%m-%d").date() - timedelta(days=29)).isoformat()
    return {
        "matched": True,
        "phrase": "最近30天",
        "dataset_current_date": end,
        "start": start,
        "end": end,
    }
