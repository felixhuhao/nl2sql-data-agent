import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.metadata.models import (
    DEFAULT_DATASOURCE,
    MetaAnalysisSpace,
    MetaColumn,
    MetaColumnAlias,
    MetaMetric,
    MetaTable,
    MetaVerifiedQuery,
    create_metadata_schema,
)
from backend.app.metadata.seed import seed_semantics


CLICKHOUSE_DATASOURCE = "clickhouse_ecommerce"


def test_seed_semantics_writes_phase2_assets():
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    with Session(engine) as session:
        _insert_physical_metadata(session)

        counts = seed_semantics(session)
        session.commit()

        assert counts["metrics"] == 3
        assert counts["verified_queries"] == 4
        assert counts["analysis_spaces"] == 1
        assert session.scalar(select(MetaMetric).where(MetaMetric.name == "sales_amount")).label == "销售额"
        assert session.scalar(select(MetaColumnAlias).where(MetaColumnAlias.alias == "销售额")).column_name == "payment_amount"
        assert session.scalar(select(MetaVerifiedQuery).where(MetaVerifiedQuery.query_id == "recent_30d_daily_sales")).sql
        space = session.scalar(select(MetaAnalysisSpace).where(MetaAnalysisSpace.name == "ecommerce_demo"))
        assert "fact_orders" in json.loads(space.tables)
        payment_amount = _column(session, "payment_amount")
        date_value = _column(session, "date_value")
        region_group = _column(session, "region_group")
        assert payment_amount.description == "订单实付金额，销售额口径字段"
        assert payment_amount.is_metric is True
        assert date_value.is_dimension is True
        assert json.loads(region_group.sample_values) == ["华东", "华北", "华南", "西南", "华中"]


def test_seed_semantics_writes_independent_datasource_assets():
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    with Session(engine) as session:
        _insert_physical_metadata(session, datasource_name=DEFAULT_DATASOURCE)
        _insert_physical_metadata(session, datasource_name=CLICKHOUSE_DATASOURCE)

        seed_semantics(session, datasource_name=DEFAULT_DATASOURCE)
        seed_semantics(session, datasource_name=CLICKHOUSE_DATASOURCE)
        session.commit()

        metrics = session.scalars(select(MetaMetric).where(MetaMetric.name == "sales_amount")).all()
        spaces = session.scalars(select(MetaAnalysisSpace).order_by(MetaAnalysisSpace.datasource)).all()

        assert {metric.datasource for metric in metrics} == {DEFAULT_DATASOURCE, CLICKHOUSE_DATASOURCE}
        assert {space.datasource for space in spaces} == {DEFAULT_DATASOURCE, CLICKHOUSE_DATASOURCE}
        assert {space.name for space in spaces} == {
            "ecommerce_demo",
            "ecommerce_demo_clickhouse_ecommerce",
        }


def test_seed_semantics_does_not_override_existing_values_by_default():
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    with Session(engine) as session:
        _insert_physical_metadata(session)
        table = session.scalar(select(MetaTable).where(MetaTable.table_name == "fact_orders"))
        table.description = "人工修改描述"
        metric = MetaMetric(
            name="sales_amount",
            label="人工销售额",
            expression="SUM(custom_amount)",
        )
        session.add(metric)

        seed_semantics(session)
        session.commit()

        assert table.description == "人工修改描述"
        assert metric.label == "人工销售额"
        assert metric.expression == "SUM(custom_amount)"


def test_seed_semantics_reset_overrides_seeded_values():
    engine = create_engine("sqlite:///:memory:")
    create_metadata_schema(engine)
    with Session(engine) as session:
        _insert_physical_metadata(session)
        metric = MetaMetric(
            name="sales_amount",
            label="人工销售额",
            expression="SUM(custom_amount)",
        )
        session.add(metric)

        seed_semantics(session, reset=True)
        session.commit()

        assert metric.label == "销售额"
        assert metric.expression == "SUM(fact_orders.payment_amount)"


def _insert_physical_metadata(session: Session, datasource_name: str = DEFAULT_DATASOURCE) -> None:
    fact_orders = MetaTable(datasource=datasource_name, table_name="fact_orders")
    dim_date = MetaTable(datasource=datasource_name, table_name="dim_date")
    dim_regions = MetaTable(datasource=datasource_name, table_name="dim_regions")
    session.add_all([fact_orders, dim_date, dim_regions])
    session.flush()
    session.add_all(
        [
            MetaColumn(
                datasource=datasource_name,
                table_id=fact_orders.id,
                column_name="payment_amount",
                data_type="DECIMAL",
            ),
            MetaColumn(datasource=datasource_name, table_id=fact_orders.id, column_name="order_id", data_type="VARCHAR"),
            MetaColumn(datasource=datasource_name, table_id=dim_date.id, column_name="date_value", data_type="DATE"),
            MetaColumn(
                datasource=datasource_name,
                table_id=dim_regions.id,
                column_name="region_group",
                data_type="VARCHAR",
            ),
        ]
    )


def _column(session: Session, column_name: str) -> MetaColumn:
    return session.scalar(select(MetaColumn).where(MetaColumn.column_name == column_name))
