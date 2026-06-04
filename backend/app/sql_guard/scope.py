from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select

from backend.app.core.db import get_sqlite_engine, sqlite_session
from backend.app.metadata.models import DEFAULT_DATASOURCE, MetaAnalysisSpace, MetaColumn, MetaTable, create_metadata_schema


@dataclass(frozen=True)
class GuardScope:
    allowed_tables: frozenset[str]
    table_columns: dict[str, frozenset[str]]

    def columns_for_table(self, table_name: str) -> frozenset[str]:
        return self.table_columns.get(table_name, frozenset())


def build_default_guard_scope(datasource_name: str = DEFAULT_DATASOURCE) -> GuardScope:
    create_metadata_schema(get_sqlite_engine())

    with sqlite_session() as session:
        allowed_tables = _active_allowed_tables(session, datasource_name=datasource_name)
        tables = session.scalars(
            select(MetaTable)
            .where(
                MetaTable.enabled.is_(True),
                MetaTable.datasource == datasource_name,
                MetaTable.table_name.in_(allowed_tables),
            )
            .order_by(MetaTable.table_name)
        ).all()

        table_columns: dict[str, frozenset[str]] = {}
        for table in tables:
            columns = session.scalars(
                select(MetaColumn.column_name)
                .where(MetaColumn.table_id == table.id)
                .order_by(MetaColumn.id)
            ).all()
            table_columns[table.table_name] = frozenset(columns)

    return GuardScope(allowed_tables=allowed_tables, table_columns=table_columns)


def _active_allowed_tables(session, datasource_name: str = DEFAULT_DATASOURCE) -> frozenset[str]:
    analysis_space = _active_analysis_space(session, datasource_name=datasource_name)
    if analysis_space is None:
        return frozenset()
    return frozenset(_parse_json_list(analysis_space.tables))


def _active_analysis_space(session, datasource_name: str = DEFAULT_DATASOURCE) -> MetaAnalysisSpace | None:
    analysis_space = session.scalar(
        select(MetaAnalysisSpace)
        .where(
            MetaAnalysisSpace.enabled.is_(True),
            MetaAnalysisSpace.datasource == datasource_name,
        )
        .order_by(MetaAnalysisSpace.id)
    )
    if analysis_space is None and datasource_name == DEFAULT_DATASOURCE:
        return session.scalar(
            select(MetaAnalysisSpace)
            .where(MetaAnalysisSpace.enabled.is_(True))
            .order_by(MetaAnalysisSpace.id)
        )
    return analysis_space


def _parse_json_list(value: str | None) -> list:
    if value is None:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
