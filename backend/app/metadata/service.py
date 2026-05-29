from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.core.db import get_sqlite_engine, sqlite_session
from backend.app.metadata.models import MetaColumn, MetaRelationship, MetaTable, create_metadata_schema


def list_tables() -> list[dict]:
    _ensure_schema()
    with sqlite_session() as session:
        tables = session.scalars(
            select(MetaTable).where(MetaTable.enabled.is_(True)).order_by(MetaTable.table_name)
        ).all()
        return [
            {
                "table_name": table.table_name,
                "display_name": table.display_name,
                "description": table.description,
                "domain": table.domain,
                "row_count": table.row_count,
            }
            for table in tables
        ]


def list_columns(table_name: str) -> list[dict]:
    _ensure_schema()
    with sqlite_session() as session:
        table = session.scalar(select(MetaTable).where(MetaTable.table_name == table_name))
        if table is None:
            return []
        columns = session.scalars(
            select(MetaColumn)
            .where(MetaColumn.table_id == table.id)
            .order_by(MetaColumn.id)
        ).all()
        return [
            {
                "column_name": column.column_name,
                "data_type": column.data_type,
                "description": column.description,
                "is_dimension": column.is_dimension,
                "is_metric": column.is_metric,
                "sample_values": column.sample_values,
            }
            for column in columns
        ]


def list_relationships() -> list[dict]:
    _ensure_schema()
    with sqlite_session() as session:
        relationships = session.scalars(
            select(MetaRelationship).order_by(
                MetaRelationship.source_table,
                MetaRelationship.target_table,
            )
        ).all()
        return [
            {
                "source_table": relationship.source_table,
                "source_column": relationship.source_column,
                "target_table": relationship.target_table,
                "target_column": relationship.target_column,
                "relationship_type": relationship.relationship_type,
                "source": relationship.source,
                "confidence": relationship.confidence,
                "fanout_risk": relationship.fanout_risk,
                "description": relationship.description,
            }
            for relationship in relationships
        ]


def build_schema_context() -> str:
    _ensure_schema()
    settings = get_settings()
    with sqlite_session() as session:
        tables = session.scalars(
            select(MetaTable).where(MetaTable.enabled.is_(True)).order_by(MetaTable.table_name)
        ).all()
        relationships = session.scalars(
            select(MetaRelationship).order_by(
                MetaRelationship.source_table,
                MetaRelationship.target_table,
            )
        ).all()
        lines = [
            "# Schema Context",
            "",
            f"dataset_current_date = {settings.dataset_current_date}",
            "relative_date_rule: 最近30天 = 2025-12-02 到 2025-12-31",
            "",
            "## Tables",
        ]
        for table in tables:
            lines.append(
                f"- {table.table_name}: {table.display_name or ''}; {table.description or ''}; rows={table.row_count}"
            )
            for column in _columns_for_table(session, table.id):
                flags = []
                if column.is_dimension:
                    flags.append("dimension")
                if column.is_metric:
                    flags.append("metric")
                flag_text = f" [{', '.join(flags)}]" if flags else ""
                description = f" - {column.description}" if column.description else ""
                sample_values = f" samples={column.sample_values}" if column.sample_values else ""
                lines.append(
                    f"  - {column.column_name} ({column.data_type}){flag_text}{description}{sample_values}"
                )
        lines.extend(["", "## Join Relationships"])
        for relationship in relationships:
            lines.append(
                "- "
                f"{relationship.source_table}.{relationship.source_column} -> "
                f"{relationship.target_table}.{relationship.target_column} "
                f"({relationship.relationship_type}; source={relationship.source}; "
                f"confidence={relationship.confidence:.2f}; fanout_risk={relationship.fanout_risk})"
            )
        lines.extend(
            [
                "",
                "## Metric Definitions",
                "- 销售额 = SUM(payment_amount)",
                "- 订单数 = COUNT(DISTINCT order_id)",
                "- 客单价 = SUM(payment_amount) / COUNT(DISTINCT order_id)",
            ]
        )
        return "\n".join(lines)


def _columns_for_table(session: Session, table_id: int) -> list[MetaColumn]:
    return session.scalars(
        select(MetaColumn)
        .where(MetaColumn.table_id == table_id)
        .order_by(MetaColumn.id)
    ).all()


def _ensure_schema() -> None:
    create_metadata_schema(get_sqlite_engine())
