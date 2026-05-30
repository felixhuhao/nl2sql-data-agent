import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.core.db import get_sqlite_engine, sqlite_session
from backend.app.metadata.models import (
    MetaAnalysisSpace,
    MetaColumn,
    MetaMetric,
    MetaRelationship,
    MetaTable,
    MetaVerifiedQuery,
    create_metadata_schema,
)
from backend.app.metadata.retrieval import retrieve_metadata_assets


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


def get_analysis_space() -> dict:
    _ensure_schema()
    with sqlite_session() as session:
        analysis_space = _active_analysis_space(session)
        return _analysis_space_payload(analysis_space) if analysis_space else {}


def list_verified_queries() -> list[dict]:
    _ensure_schema()
    with sqlite_session() as session:
        return [_verified_query_payload(query) for query in _verified_queries(session)]


def build_schema_context() -> str:
    _ensure_schema()
    settings = get_settings()
    with sqlite_session() as session:
        analysis_space = _active_analysis_space(session)
        analysis_space_payload = _analysis_space_payload(analysis_space) if analysis_space else _empty_analysis_space_payload()
        allowed_tables = set(analysis_space_payload["tables"])
        metrics = _enabled_metrics(session, set(analysis_space_payload["enabled_metrics"]))
        verified_queries = _verified_queries(session)
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
        table_columns = {table.table_name: _columns_for_table(session, table.id) for table in tables}
        return _render_schema_context(
            settings.dataset_current_date,
            analysis_space_payload,
            tables,
            table_columns,
            relationships,
            metrics,
            verified_queries,
        )


def build_focused_context(question: str) -> str:
    return build_focused_context_from_retrieval(retrieve_metadata_assets(question))


def build_focused_context_from_retrieval(retrieval_result: dict) -> str:
    if retrieval_result.get("fallback_used"):
        return build_schema_context()

    _ensure_schema()
    settings = get_settings()
    with sqlite_session() as session:
        analysis_space = _active_analysis_space(session)
        analysis_space_payload = _analysis_space_payload(analysis_space) if analysis_space else _empty_analysis_space_payload()
        allowed_tables = set(analysis_space_payload["tables"])

        table_names = {table["table_name"] for table in retrieval_result.get("tables", [])}
        column_keys = {
            (column["table_name"], column["column_name"])
            for column in retrieval_result.get("columns", [])
        }
        metric_names = {metric["name"] for metric in retrieval_result.get("metrics", [])}
        verified_query_ids = {query["id"] for query in retrieval_result.get("verified_queries", [])}

        relationships = _relationships_for_tables(session, allowed_tables)
        _expand_join_partners(table_names, column_keys, relationships)

        tables = _tables_by_name(session, table_names, allowed_tables)
        table_columns = _focused_columns_by_table(session, tables, column_keys)
        focused_relationships = [
            relationship
            for relationship in relationships
            if relationship.source_table in table_names and relationship.target_table in table_names
        ]
        metrics = _metrics_by_name(session, metric_names)
        verified_queries = _verified_queries_by_id(session, verified_query_ids)

        return _render_schema_context(
            settings.dataset_current_date,
            analysis_space_payload,
            tables,
            table_columns,
            focused_relationships,
            metrics,
            verified_queries,
        )


def build_explainability_context() -> dict:
    _ensure_schema()
    settings = get_settings()
    with sqlite_session() as session:
        analysis_space = _active_analysis_space(session)
        analysis_space_payload = _analysis_space_payload(analysis_space) if analysis_space else _empty_analysis_space_payload()
        allowed_tables = set(analysis_space_payload["tables"])
        metrics = _enabled_metrics(session, set(analysis_space_payload["enabled_metrics"]))
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
            "analysis_space": analysis_space_payload,
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
            "metrics": [_metric_payload(metric) for metric in metrics],
            "join_paths": [_relationship_explainability(relationship) for relationship in relationships],
            "verified_queries": [_verified_query_payload(query) for query in _verified_queries(session)],
        }


def _columns_for_table(session: Session, table_id: int) -> list[MetaColumn]:
    return session.scalars(
        select(MetaColumn)
        .where(MetaColumn.table_id == table_id)
        .order_by(MetaColumn.id)
    ).all()


def _render_schema_context(
    dataset_current_date: str,
    analysis_space_payload: dict,
    tables: list[MetaTable],
    table_columns: dict[str, list[MetaColumn]],
    relationships: list[MetaRelationship],
    metrics: list[MetaMetric],
    verified_queries: list[MetaVerifiedQuery],
) -> str:
    lines = [
        "# Schema Context",
        "",
        "## Analysis Space",
        f"name = {analysis_space_payload['name']}",
        f"datasource = {analysis_space_payload['datasource']}",
        f"allowed_operations = {', '.join(analysis_space_payload['allowed_operations'])}",
        f"enabled_metrics = {', '.join(analysis_space_payload['enabled_metrics'])}",
        f"allowed_tables = {', '.join(analysis_space_payload['tables'])}",
        "",
        f"dataset_current_date = {dataset_current_date}",
        "relative_date_rule: 最近30天 = 2025-12-02 到 2025-12-31",
        "",
        "## Tables",
    ]
    for table in tables:
        lines.append(
            f"- {table.table_name}: {table.display_name or ''}; {table.description or ''}; rows={table.row_count}"
        )
        for column in table_columns.get(table.table_name, []):
            lines.append(_column_context_line(column))
    lines.extend(["", "## Join Relationships"])
    for relationship in relationships:
        lines.append(_relationship_context_line(relationship))
    if metrics:
        lines.extend(["", "## Metric Definitions"])
        for metric in metrics:
            lines.append(f"- {metric.label} ({metric.name}) = {metric.expression}")
    if verified_queries:
        lines.extend(["", "## Verified Queries"])
        for query in verified_queries:
            lines.extend(
                [
                    f"- id: {query.query_id}",
                    f"  question: {query.question}",
                    f"  sql: {query.sql}",
                    f"  tags: {', '.join(_parse_json_list(query.tags))}",
                ]
            )
    return "\n".join(lines)


def _column_context_line(column: MetaColumn) -> str:
    flags = []
    if column.is_dimension:
        flags.append("dimension")
    if column.is_metric:
        flags.append("metric")
    flag_text = f" [{', '.join(flags)}]" if flags else ""
    description = f" - {column.description}" if column.description else ""
    sample_values = f" samples={column.sample_values}" if column.sample_values else ""
    return f"  - {column.column_name} ({column.data_type}){flag_text}{description}{sample_values}"


def _relationship_context_line(relationship: MetaRelationship) -> str:
    return (
        "- "
        f"{relationship.source_table}.{relationship.source_column} -> "
        f"{relationship.target_table}.{relationship.target_column} "
        f"({relationship.relationship_type}; source={relationship.source}; "
        f"confidence={relationship.confidence:.2f}; fanout_risk={relationship.fanout_risk})"
    )


def _tables_by_name(session: Session, table_names: set[str], allowed_tables: set[str]) -> list[MetaTable]:
    selected_tables = table_names & allowed_tables
    if not selected_tables:
        return []
    return session.scalars(
        select(MetaTable)
        .where(MetaTable.enabled.is_(True), MetaTable.table_name.in_(selected_tables))
        .order_by(MetaTable.table_name)
    ).all()


def _focused_columns_by_table(
    session: Session,
    tables: list[MetaTable],
    column_keys: set[tuple[str, str]],
) -> dict[str, list[MetaColumn]]:
    table_ids = {table.table_name: table.id for table in tables}
    columns_by_table: dict[str, list[MetaColumn]] = {table.table_name: [] for table in tables}
    if not column_keys:
        return columns_by_table

    columns = session.scalars(
        select(MetaColumn)
        .join(MetaTable)
        .where(
            MetaTable.table_name.in_(set(table_ids)),
            MetaColumn.column_name.in_({column_name for _, column_name in column_keys}),
        )
        .order_by(MetaTable.table_name, MetaColumn.id)
    ).all()
    for column in columns:
        table_name = column.table.table_name
        if (table_name, column.column_name) in column_keys:
            columns_by_table[table_name].append(column)
    return columns_by_table


def _relationships_for_tables(session: Session, allowed_tables: set[str]) -> list[MetaRelationship]:
    if not allowed_tables:
        return []
    return session.scalars(
        select(MetaRelationship)
        .where(
            MetaRelationship.source_table.in_(allowed_tables),
            MetaRelationship.target_table.in_(allowed_tables),
        )
        .order_by(MetaRelationship.source_table, MetaRelationship.target_table)
    ).all()


def _expand_join_partners(
    table_names: set[str],
    column_keys: set[tuple[str, str]],
    relationships: list[MetaRelationship],
) -> None:
    initially_selected_tables = set(table_names)
    has_fact_table = any(table_name.startswith("fact_") for table_name in table_names)
    added_fact_partners = 0
    for relationship in relationships:
        if relationship.source_table in table_names and relationship.target_table in table_names:
            column_keys.add((relationship.source_table, relationship.source_column))
            column_keys.add((relationship.target_table, relationship.target_column))
            continue

        if (
            relationship.target_table in initially_selected_tables
            and relationship.source_table.startswith("fact_")
            and not has_fact_table
            and added_fact_partners < 3
        ):
            table_names.add(relationship.source_table)
            column_keys.add((relationship.source_table, relationship.source_column))
            column_keys.add((relationship.target_table, relationship.target_column))
            added_fact_partners += 1


def _metrics_by_name(session: Session, metric_names: set[str]) -> list[MetaMetric]:
    if not metric_names:
        return []
    return session.scalars(
        select(MetaMetric)
        .where(MetaMetric.enabled.is_(True), MetaMetric.name.in_(metric_names))
        .order_by(MetaMetric.id)
    ).all()


def _verified_queries_by_id(session: Session, query_ids: set[str]) -> list[MetaVerifiedQuery]:
    if not query_ids:
        return []
    return session.scalars(
        select(MetaVerifiedQuery)
        .where(MetaVerifiedQuery.enabled.is_(True), MetaVerifiedQuery.query_id.in_(query_ids))
        .order_by(MetaVerifiedQuery.id)
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


def _active_analysis_space(session: Session) -> MetaAnalysisSpace | None:
    return session.scalar(
        select(MetaAnalysisSpace)
        .where(MetaAnalysisSpace.enabled.is_(True))
        .order_by(MetaAnalysisSpace.id)
    )


def _enabled_metrics(session: Session, enabled_metric_names: set[str]) -> list[MetaMetric]:
    if not enabled_metric_names:
        return []
    return session.scalars(
        select(MetaMetric)
        .where(MetaMetric.enabled.is_(True), MetaMetric.name.in_(enabled_metric_names))
        .order_by(MetaMetric.id)
    ).all()


def _verified_queries(session: Session) -> list[MetaVerifiedQuery]:
    return session.scalars(
        select(MetaVerifiedQuery)
        .where(MetaVerifiedQuery.enabled.is_(True))
        .order_by(MetaVerifiedQuery.id)
    ).all()


def _analysis_space_payload(analysis_space: MetaAnalysisSpace) -> dict:
    return {
        "name": analysis_space.name,
        "datasource": analysis_space.datasource,
        "tables": _parse_json_list(analysis_space.tables),
        "allowed_tables": _parse_json_list(analysis_space.tables),
        "enabled_metrics": _parse_json_list(analysis_space.enabled_metrics),
        "allowed_operations": _parse_json_list(analysis_space.allowed_operations),
    }


def _empty_analysis_space_payload() -> dict:
    return {
        "name": "",
        "datasource": "",
        "tables": [],
        "allowed_tables": [],
        "enabled_metrics": [],
        "allowed_operations": [],
    }


def _metric_payload(metric: MetaMetric) -> dict:
    return {
        "name": metric.name,
        "label": metric.label,
        "expression": metric.expression,
        "description": metric.description,
        "default_time_column": metric.default_time_column,
        "allowed_dimensions": _parse_json_list(metric.allowed_dimensions),
        "enabled": metric.enabled,
    }


def _verified_query_payload(query: MetaVerifiedQuery) -> dict:
    return {
        "id": query.query_id,
        "question": query.question,
        "sql": query.sql,
        "tags": _parse_json_list(query.tags),
        "verified_by": query.verified_by,
    }


def _parse_json_list(value: str | None) -> list:
    if value is None:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_sample_values(sample_values: str | None) -> list | None:
    if sample_values is None:
        return None
    try:
        return json.loads(sample_values)
    except json.JSONDecodeError:
        return None


def _ensure_schema() -> None:
    create_metadata_schema(get_sqlite_engine())
