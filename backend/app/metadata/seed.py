from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.dataspace.analysis_space import get_default_analysis_space
from backend.app.dataspace.verified_queries import list_verified_queries
from backend.app.metadata.models import (
    DEFAULT_DATASOURCE,
    MetaAnalysisSpace,
    MetaColumn,
    MetaColumnAlias,
    MetaMetric,
    MetaRelationship,
    MetaTable,
    MetaVerifiedQuery,
)
from backend.app.metadata.semantic_overlay import (
    COLUMN_SEMANTICS,
    CONFIRMED_RELATIONSHIPS,
    DIMENSION_COLUMNS,
    METRIC_COLUMNS,
    SAMPLE_VALUE_FALLBACKS,
    TABLE_COLUMN_SEMANTICS,
    TABLE_SEMANTICS,
)


METRIC_SEEDS = (
    {
        "name": "sales_amount",
        "label": "销售额",
        "expression": "SUM(fact_orders.payment_amount)",
        "description": "订单实付金额汇总",
        "default_time_column": "dim_date.date_value",
        "allowed_dimensions": ["date", "channel", "region", "category"],
    },
    {
        "name": "order_count",
        "label": "订单数",
        "expression": "COUNT(DISTINCT fact_orders.order_id)",
        "description": "去重订单数",
        "default_time_column": "dim_date.date_value",
        "allowed_dimensions": ["date", "channel", "region"],
    },
    {
        "name": "aov",
        "label": "客单价",
        "expression": "SUM(fact_orders.payment_amount) / COUNT(DISTINCT fact_orders.order_id)",
        "description": "销售额除以订单数",
        "default_time_column": "dim_date.date_value",
        "allowed_dimensions": ["date", "channel", "region"],
    },
)

COLUMN_ALIAS_SEEDS = (
    ("fact_orders", "payment_amount", ("销售额", "成交额", "实付金额", "GMV")),
    ("fact_orders", "order_id", ("订单", "订单数")),
    ("fact_orders", "date_key", ("下单日期", "订单日期")),
    ("fact_orders", "region_key", ("地区", "区域", "大区")),
    ("fact_orders", "channel_key", ("渠道", "来源渠道")),
    ("fact_order_items", "quantity", ("销量", "销售件数", "件数")),
    ("fact_order_items", "item_amount", ("明细销售额", "商品销售额")),
    ("dim_date", "date_value", ("日期", "时间", "天")),
    ("dim_regions", "region_group", ("地区", "大区", "区域")),
    ("dim_regions", "province", ("省份", "省")),
    ("dim_regions", "city", ("城市", "市")),
    ("dim_channels", "channel_name", ("渠道", "销售渠道", "来源")),
    ("dim_channels", "channel_type", ("渠道类型", "渠道分类")),
    ("dim_products", "name", ("商品", "商品名称", "SKU")),
    ("dim_products", "category", ("品类", "一级品类", "类目")),
    ("dim_products", "sub_category", ("子品类", "二级品类")),
    ("dim_products", "brand", ("品牌",)),
)


def seed_semantics(
    session: Session,
    reset: bool = False,
    datasource_name: str = DEFAULT_DATASOURCE,
) -> dict[str, int]:
    counts = {
        "tables": _seed_table_semantics(session, reset, datasource_name),
        "columns": _seed_column_semantics(session, reset, datasource_name),
        "relationships": _seed_relationships(session, reset, datasource_name),
        "metrics": _seed_metrics(session, reset, datasource_name),
        "aliases": _seed_aliases(session, datasource_name),
        "verified_queries": _seed_verified_queries(session, reset, datasource_name),
        "analysis_spaces": _seed_analysis_spaces(session, reset, datasource_name),
    }
    session.flush()
    return counts


def _seed_table_semantics(session: Session, reset: bool, datasource_name: str) -> int:
    count = 0
    for table_name, (display_name, description, domain) in TABLE_SEMANTICS.items():
        table = session.scalar(
            select(MetaTable).where(MetaTable.datasource == datasource_name, MetaTable.table_name == table_name)
        )
        if table is None:
            continue
        if reset or table.display_name is None:
            table.display_name = display_name
        if reset or table.description is None:
            table.description = description
        if reset or table.domain is None:
            table.domain = domain
        count += 1
    return count


def _seed_column_semantics(session: Session, reset: bool, datasource_name: str) -> int:
    columns = session.scalars(
        select(MetaColumn).join(MetaTable).where(MetaTable.datasource == datasource_name)
    ).all()
    count = 0
    for column in columns:
        table_name = column.table.table_name
        column_name = column.column_name
        description = TABLE_COLUMN_SEMANTICS.get((table_name, column_name), COLUMN_SEMANTICS.get(column_name))
        if description and (reset or column.description is None):
            column.description = description
        if reset or not column.is_dimension:
            column.is_dimension = column_name in DIMENSION_COLUMNS
        if reset or not column.is_metric:
            column.is_metric = column_name in METRIC_COLUMNS
        if column.sample_values is None:
            fallback_values = SAMPLE_VALUE_FALLBACKS.get(column_name)
            if fallback_values:
                column.sample_values = json.dumps(fallback_values, ensure_ascii=False)
        count += 1
    return count


def _seed_relationships(session: Session, reset: bool, datasource_name: str) -> int:
    count = 0
    for source_table, source_column, target_table, target_column, relationship_type, description in CONFIRMED_RELATIONSHIPS:
        relationship = session.scalar(
            select(MetaRelationship).where(
                MetaRelationship.source_table == source_table,
                MetaRelationship.source_column == source_column,
                MetaRelationship.target_table == target_table,
                MetaRelationship.target_column == target_column,
                MetaRelationship.datasource == datasource_name,
            )
        )
        if relationship is None:
            relationship = MetaRelationship(
                datasource=datasource_name,
                source_table=source_table,
                source_column=source_column,
                target_table=target_table,
                target_column=target_column,
            )
            session.add(relationship)
        if reset or relationship.source in {None, "inferred"}:
            relationship.relationship_type = relationship_type
            relationship.source = "overlay"
            relationship.confidence = 1.0
            relationship.fanout_risk = _fanout_risk(source_table, target_table, relationship_type)
            relationship.description = description
        count += 1
    return count


def _seed_metrics(session: Session, reset: bool, datasource_name: str) -> int:
    count = 0
    for seed in METRIC_SEEDS:
        metric = session.scalar(
            select(MetaMetric).where(MetaMetric.datasource == datasource_name, MetaMetric.name == seed["name"])
        )
        if metric is None:
            metric = MetaMetric(
                datasource=datasource_name,
                name=seed["name"],
                label=seed["label"],
                expression=seed["expression"],
            )
            session.add(metric)
        if reset:
            metric.label = seed["label"]
            metric.expression = seed["expression"]
            metric.description = seed["description"]
            metric.default_time_column = seed["default_time_column"]
            metric.allowed_dimensions = json.dumps(seed["allowed_dimensions"], ensure_ascii=False)
        else:
            if metric.description is None:
                metric.description = seed["description"]
            if metric.default_time_column is None:
                metric.default_time_column = seed["default_time_column"]
            if metric.allowed_dimensions is None:
                metric.allowed_dimensions = json.dumps(seed["allowed_dimensions"], ensure_ascii=False)
        metric.enabled = True
        count += 1
    return count


def _seed_aliases(session: Session, datasource_name: str) -> int:
    count = 0
    for table_name, column_name, aliases in COLUMN_ALIAS_SEEDS:
        for alias in aliases:
            existing = session.scalar(
                select(MetaColumnAlias).where(
                    MetaColumnAlias.table_name == table_name,
                    MetaColumnAlias.column_name == column_name,
                    MetaColumnAlias.alias == alias,
                    MetaColumnAlias.datasource == datasource_name,
                )
            )
            if existing is None:
                session.add(
                    MetaColumnAlias(
                        datasource=datasource_name,
                        table_name=table_name,
                        column_name=column_name,
                        alias=alias,
                    )
                )
            count += 1
    return count


def _seed_verified_queries(session: Session, reset: bool, datasource_name: str) -> int:
    count = 0
    for query in list_verified_queries():
        verified_query = session.scalar(
            select(MetaVerifiedQuery).where(
                MetaVerifiedQuery.datasource == datasource_name,
                MetaVerifiedQuery.query_id == query.id,
            )
        )
        if verified_query is None:
            verified_query = MetaVerifiedQuery(
                datasource=datasource_name,
                query_id=query.id,
                question=query.question,
                sql=query.sql,
            )
            session.add(verified_query)
        if reset or not verified_query.question:
            verified_query.question = query.question
        if reset or not verified_query.sql:
            verified_query.sql = query.sql
        if reset or verified_query.tags is None:
            verified_query.tags = json.dumps(list(query.tags), ensure_ascii=False)
        if reset or not verified_query.verified_by:
            verified_query.verified_by = query.verified_by
        verified_query.enabled = True
        count += 1
    return count


def _seed_analysis_spaces(session: Session, reset: bool, datasource_name: str) -> int:
    space = get_default_analysis_space()
    space_name = _analysis_space_name(space.name, datasource_name)
    meta_space = session.scalar(
        select(MetaAnalysisSpace).where(
            MetaAnalysisSpace.datasource == datasource_name,
            MetaAnalysisSpace.name == space_name,
        )
    )
    if meta_space is None:
        meta_space = MetaAnalysisSpace(
            name=space_name,
            datasource=datasource_name,
            tables=json.dumps(list(space.tables), ensure_ascii=False),
            enabled_metrics=json.dumps(list(space.enabled_metrics), ensure_ascii=False),
            allowed_operations=json.dumps(list(space.allowed_operations), ensure_ascii=False),
        )
        session.add(meta_space)
    if reset or meta_space.datasource == "":
        meta_space.datasource = datasource_name
    if reset or meta_space.tables == "":
        meta_space.tables = json.dumps(list(space.tables), ensure_ascii=False)
    if reset or meta_space.enabled_metrics == "":
        meta_space.enabled_metrics = json.dumps(list(space.enabled_metrics), ensure_ascii=False)
    if reset or meta_space.allowed_operations == "":
        meta_space.allowed_operations = json.dumps(list(space.allowed_operations), ensure_ascii=False)
    meta_space.enabled = True
    return 1


def _analysis_space_name(default_name: str, datasource_name: str) -> str:
    if datasource_name == DEFAULT_DATASOURCE:
        return default_name
    return f"{default_name}_{datasource_name}"


def _fanout_risk(source_table: str, target_table: str, relationship_type: str) -> str:
    if relationship_type == "one_to_many":
        return "high"
    if source_table == "fact_order_items" and target_table == "fact_orders":
        return "medium"
    return "low"
