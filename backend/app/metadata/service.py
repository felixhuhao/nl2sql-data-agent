import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.core.db import get_sqlite_engine, sqlite_session
from backend.app.dataspace.analysis_space import get_default_analysis_space
from backend.app.dataspace.verified_queries import list_verified_queries
from backend.app.metadata.models import MetaColumn, MetaRelationship, MetaTable, create_metadata_schema


METRIC_DEFINITIONS = [
    {
        "name": "sales_amount",
        "label": "销售额",
        "expression": "SUM(fact_orders.payment_amount)",
    },
    {
        "name": "order_count",
        "label": "订单数",
        "expression": "COUNT(DISTINCT fact_orders.order_id)",
    },
    {
        "name": "aov",
        "label": "客单价",
        "expression": "SUM(fact_orders.payment_amount) / COUNT(DISTINCT fact_orders.order_id)",
    },
]


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
    analysis_space = get_default_analysis_space()
    allowed_tables = set(analysis_space.tables)
    with sqlite_session() as session:
        tables = session.scalars(
            select(MetaTable)
            .where(MetaTable.enabled.is_(True), MetaTable.table_name.in_(allowed_tables))
            .order_by(MetaTable.table_name)
        ).all()
        relationships = session.scalars(
            select(MetaRelationship)
            .where(
                MetaRelationship.source_table.in_(allowed_tables),
                MetaRelationship.target_table.in_(allowed_tables),
            )
            .order_by(MetaRelationship.source_table, MetaRelationship.target_table)
        ).all()
        lines = [
            "# Schema Context",
            "",
            "## Analysis Space",
            f"name = {analysis_space.name}",
            f"datasource = {analysis_space.datasource}",
            f"allowed_operations = {', '.join(analysis_space.allowed_operations)}",
            f"enabled_metrics = {', '.join(analysis_space.enabled_metrics)}",
            f"allowed_tables = {', '.join(analysis_space.tables)}",
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
        verified_queries = list_verified_queries()
        if verified_queries:
            lines.extend(["", "## Verified Queries"])
            for query in verified_queries:
                lines.extend(
                    [
                        f"- id: {query.id}",
                        f"  question: {query.question}",
                        f"  sql: {query.sql}",
                        f"  tags: {', '.join(query.tags)}",
                    ]
                )
        return "\n".join(lines)


def build_explainability_context() -> dict:
    _ensure_schema()
    settings = get_settings()
    analysis_space = get_default_analysis_space()
    allowed_tables = set(analysis_space.tables)
    with sqlite_session() as session:
        tables = session.scalars(
            select(MetaTable)
            .where(MetaTable.enabled.is_(True), MetaTable.table_name.in_(allowed_tables))
            .order_by(MetaTable.table_name)
        ).all()
        relationships = session.scalars(
            select(MetaRelationship)
            .where(
                MetaRelationship.source_table.in_(allowed_tables),
                MetaRelationship.target_table.in_(allowed_tables),
            )
            .order_by(MetaRelationship.source_table, MetaRelationship.target_table)
        ).all()
        return {
            "analysis_space": {
                "name": analysis_space.name,
                "datasource": analysis_space.datasource,
                "allowed_tables": list(analysis_space.tables),
                "enabled_metrics": list(analysis_space.enabled_metrics),
                "allowed_operations": list(analysis_space.allowed_operations),
            },
            "date_rule": {
                "dataset_current_date": settings.dataset_current_date,
                "relative_rules": {
                    "最近30天": {
                        "start": "2025-12-02",
                        "end": "2025-12-31",
                    }
                },
            },
            "tables": [_table_explainability(session, table) for table in tables],
            "metrics": [
                metric
                for metric in METRIC_DEFINITIONS
                if metric["name"] in analysis_space.enabled_metrics
            ],
            "join_paths": [_relationship_explainability(relationship) for relationship in relationships],
            "verified_queries": [
                {
                    "id": query.id,
                    "question": query.question,
                    "sql": query.sql,
                    "tags": list(query.tags),
                    "verified_by": query.verified_by,
                }
                for query in list_verified_queries()
            ],
        }


def _columns_for_table(session: Session, table_id: int) -> list[MetaColumn]:
    return session.scalars(
        select(MetaColumn)
        .where(MetaColumn.table_id == table_id)
        .order_by(MetaColumn.id)
    ).all()


def _table_explainability(session: Session, table: MetaTable) -> dict:
    return {
        "table_name": table.table_name,
        "display_name": table.display_name,
        "description": table.description,
        "domain": table.domain,
        "row_count": table.row_count,
        "columns": [
            {
                "column_name": column.column_name,
                "data_type": column.data_type,
                "description": column.description,
                "is_dimension": column.is_dimension,
                "is_metric": column.is_metric,
                "sample_values": _parse_sample_values(column.sample_values),
            }
            for column in _columns_for_table(session, table.id)
        ],
    }


def _relationship_explainability(relationship: MetaRelationship) -> dict:
    return {
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


def _parse_sample_values(sample_values: str | None) -> list | None:
    if sample_values is None:
        return None
    try:
        return json.loads(sample_values)
    except json.JSONDecodeError:
        return None


def _ensure_schema() -> None:
    create_metadata_schema(get_sqlite_engine())
