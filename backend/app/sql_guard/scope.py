from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from backend.app.core.db import get_sqlite_engine, sqlite_session
from backend.app.dataspace.analysis_space import get_default_analysis_space
from backend.app.metadata.models import MetaColumn, MetaTable, create_metadata_schema


@dataclass(frozen=True)
class GuardScope:
    allowed_tables: frozenset[str]
    table_columns: dict[str, frozenset[str]]

    def columns_for_table(self, table_name: str) -> frozenset[str]:
        return self.table_columns.get(table_name, frozenset())


def build_default_guard_scope() -> GuardScope:
    create_metadata_schema(get_sqlite_engine())
    analysis_space = get_default_analysis_space()
    allowed_tables = frozenset(analysis_space.tables)

    with sqlite_session() as session:
        tables = session.scalars(
            select(MetaTable)
            .where(MetaTable.enabled.is_(True), MetaTable.table_name.in_(allowed_tables))
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
