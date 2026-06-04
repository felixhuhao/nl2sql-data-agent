from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


DEFAULT_DATASOURCE = "duckdb_ecommerce"
METADATA_TABLES = (
    "meta_columns",
    "meta_relationships",
    "meta_metrics",
    "meta_column_aliases",
    "meta_verified_queries",
    "meta_analysis_spaces",
    "meta_tables",
)


class Base(DeclarativeBase):
    pass


class MetaTable(Base):
    __tablename__ = "meta_tables"
    __table_args__ = (UniqueConstraint("datasource", "table_name", name="uq_meta_tables_datasource_table"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    datasource: Mapped[str] = mapped_column(
        String,
        default=DEFAULT_DATASOURCE,
        server_default=DEFAULT_DATASOURCE,
        nullable=False,
        index=True,
    )
    table_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    engine: Mapped[str] = mapped_column(String, default="", server_default="")
    partition_key: Mapped[str] = mapped_column(Text, default="", server_default="")
    sorting_key: Mapped[str] = mapped_column(Text, default="", server_default="")

    columns: Mapped[list["MetaColumn"]] = relationship(
        back_populates="table",
        cascade="all, delete-orphan",
        order_by="MetaColumn.id",
    )


class MetaColumn(Base):
    __tablename__ = "meta_columns"
    __table_args__ = (
        UniqueConstraint("datasource", "table_id", "column_name", name="uq_meta_columns_datasource_table_column"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    datasource: Mapped[str] = mapped_column(
        String,
        default=DEFAULT_DATASOURCE,
        server_default=DEFAULT_DATASOURCE,
        nullable=False,
        index=True,
    )
    table_id: Mapped[int] = mapped_column(ForeignKey("meta_tables.id"), nullable=False)
    column_name: Mapped[str] = mapped_column(String, nullable=False)
    data_type: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    nullable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_dimension: Mapped[bool] = mapped_column(Boolean, default=False)
    is_metric: Mapped[bool] = mapped_column(Boolean, default=False)
    sample_values: Mapped[str | None] = mapped_column(Text)
    is_partition_key: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_sorting_key: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_primary_key: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    low_cardinality: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    table: Mapped[MetaTable] = relationship(back_populates="columns")


class MetaRelationship(Base):
    __tablename__ = "meta_relationships"
    __table_args__ = (
        UniqueConstraint(
            "datasource",
            "source_table",
            "source_column",
            "target_table",
            "target_column",
            name="uq_meta_relationships_datasource_edge",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    datasource: Mapped[str] = mapped_column(
        String,
        default=DEFAULT_DATASOURCE,
        server_default=DEFAULT_DATASOURCE,
        nullable=False,
        index=True,
    )
    source_table: Mapped[str] = mapped_column(String, nullable=False)
    source_column: Mapped[str] = mapped_column(String, nullable=False)
    target_table: Mapped[str] = mapped_column(String, nullable=False)
    target_column: Mapped[str] = mapped_column(String, nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, default="many_to_one")
    source: Mapped[str] = mapped_column(String, default="inferred")
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    fanout_risk: Mapped[str] = mapped_column(String, default="low")
    description: Mapped[str | None] = mapped_column(Text)


class MetaMetric(Base):
    __tablename__ = "meta_metrics"
    __table_args__ = (UniqueConstraint("datasource", "name", name="uq_meta_metrics_datasource_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    datasource: Mapped[str] = mapped_column(
        String,
        default=DEFAULT_DATASOURCE,
        server_default=DEFAULT_DATASOURCE,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    default_time_column: Mapped[str | None] = mapped_column(String)
    allowed_dimensions: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class MetaColumnAlias(Base):
    __tablename__ = "meta_column_aliases"
    __table_args__ = (
        UniqueConstraint("datasource", "table_name", "column_name", "alias", name="uq_meta_column_aliases"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    datasource: Mapped[str] = mapped_column(
        String,
        default=DEFAULT_DATASOURCE,
        server_default=DEFAULT_DATASOURCE,
        nullable=False,
        index=True,
    )
    table_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    column_name: Mapped[str] = mapped_column(String, nullable=False)
    alias: Mapped[str] = mapped_column(String, nullable=False, index=True)


class MetaVerifiedQuery(Base):
    __tablename__ = "meta_verified_queries"
    __table_args__ = (UniqueConstraint("datasource", "query_id", name="uq_meta_verified_queries_datasource_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    datasource: Mapped[str] = mapped_column(
        String,
        default=DEFAULT_DATASOURCE,
        server_default=DEFAULT_DATASOURCE,
        nullable=False,
        index=True,
    )
    query_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[str | None] = mapped_column(Text)
    verified_by: Mapped[str] = mapped_column(String, default="system")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class MetaAnalysisSpace(Base):
    __tablename__ = "meta_analysis_spaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    datasource: Mapped[str] = mapped_column(String, nullable=False)
    tables: Mapped[str] = mapped_column(Text, nullable=False)
    enabled_metrics: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_operations: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


def create_metadata_schema(engine) -> None:
    _migrate_legacy_datasource_schema(engine)
    Base.metadata.create_all(engine)
    _ensure_metadata_extension_columns(engine)


def _ensure_metadata_extension_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        if "meta_tables" in existing_tables:
            existing_columns = {column["name"] for column in inspector.get_columns("meta_tables")}
            for column_name, ddl in {
                "engine": "TEXT DEFAULT ''",
                "partition_key": "TEXT DEFAULT ''",
                "sorting_key": "TEXT DEFAULT ''",
            }.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE meta_tables ADD COLUMN {column_name} {ddl}"))

        if "meta_columns" in existing_tables:
            existing_columns = {column["name"] for column in inspector.get_columns("meta_columns")}
            for column_name, ddl in {
                "nullable": "BOOLEAN DEFAULT 0",
                "is_partition_key": "BOOLEAN DEFAULT 0",
                "is_sorting_key": "BOOLEAN DEFAULT 0",
                "is_primary_key": "BOOLEAN DEFAULT 0",
                "low_cardinality": "BOOLEAN DEFAULT 0",
            }.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE meta_columns ADD COLUMN {column_name} {ddl}"))


def _migrate_legacy_datasource_schema(engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    if "meta_tables_legacy" in existing_tables:
        _finish_legacy_datasource_schema_migration(engine)
        return
    if "meta_tables" not in existing_tables:
        return
    existing_columns = {column["name"] for column in inspector.get_columns("meta_tables")}
    if "datasource" in existing_columns:
        return
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Legacy metadata schema migration is only implemented for SQLite.")

    legacy_tables = [table_name for table_name in METADATA_TABLES if table_name in existing_tables]
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        for table_name in legacy_tables:
            connection.execute(text(f"ALTER TABLE {table_name} RENAME TO {table_name}_legacy"))
            _drop_legacy_indexes(connection, f"{table_name}_legacy")

        _create_and_copy_from_legacy(connection)
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _finish_legacy_datasource_schema_migration(engine) -> None:
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Legacy metadata schema migration is only implemented for SQLite.")
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        for table_name in reversed(METADATA_TABLES):
            connection.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
        for table_name in METADATA_TABLES:
            _drop_legacy_indexes(connection, f"{table_name}_legacy")
        _create_and_copy_from_legacy(connection)
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _create_and_copy_from_legacy(connection) -> None:
    Base.metadata.create_all(connection)

    _copy_legacy_rows(
        connection,
        "meta_tables",
        ["id", "datasource", "table_name", "display_name", "description", "domain", "row_count", "enabled"],
    )
    _copy_legacy_rows(
        connection,
        "meta_columns",
        [
            "id",
            "datasource",
            "table_id",
            "column_name",
            "data_type",
            "description",
            "is_dimension",
            "is_metric",
            "sample_values",
        ],
    )
    _copy_legacy_rows(
        connection,
        "meta_relationships",
        [
            "id",
            "datasource",
            "source_table",
            "source_column",
            "target_table",
            "target_column",
            "relationship_type",
            "source",
            "confidence",
            "fanout_risk",
            "description",
        ],
    )
    _copy_legacy_rows(
        connection,
        "meta_metrics",
        [
            "id",
            "datasource",
            "name",
            "label",
            "expression",
            "description",
            "default_time_column",
            "allowed_dimensions",
            "enabled",
        ],
    )
    _copy_legacy_rows(
        connection,
        "meta_column_aliases",
        ["id", "datasource", "table_name", "column_name", "alias"],
    )
    _copy_legacy_rows(
        connection,
        "meta_verified_queries",
        ["id", "datasource", "query_id", "question", "sql", "tags", "verified_by", "enabled"],
    )
    _copy_legacy_rows(
        connection,
        "meta_analysis_spaces",
        ["id", "name", "datasource", "tables", "enabled_metrics", "allowed_operations", "enabled"],
    )

    for table_name in reversed(METADATA_TABLES):
        connection.execute(text(f"DROP TABLE IF EXISTS {table_name}_legacy"))


def _drop_legacy_indexes(connection, legacy_table_name: str) -> None:
    if not _table_exists(connection, legacy_table_name):
        return
    rows = connection.execute(text(f"PRAGMA index_list({_quote_sqlite_identifier(legacy_table_name)})")).fetchall()
    for row in rows:
        index_name = row[1]
        if str(index_name).startswith("sqlite_autoindex"):
            continue
        connection.execute(text(f"DROP INDEX IF EXISTS {_quote_sqlite_identifier(str(index_name))}"))


def _copy_legacy_rows(connection, table_name: str, target_columns: list[str]) -> None:
    legacy_table_name = f"{table_name}_legacy"
    if not _table_exists(connection, legacy_table_name):
        return

    legacy_columns = {
        row[1]
        for row in connection.execute(
            text(f"PRAGMA table_info({_quote_sqlite_identifier(legacy_table_name)})")
        ).fetchall()
    }
    target_exprs = [_legacy_column_expr(column, legacy_columns) for column in target_columns]
    connection.execute(
        text(
            f"INSERT INTO {table_name} ({', '.join(target_columns)}) "
            f"SELECT {', '.join(target_exprs)} FROM {legacy_table_name}"
        )
    )


def _table_exists(connection, table_name: str) -> bool:
    row = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :table_name"),
        {"table_name": table_name},
    ).fetchone()
    return row is not None


def _quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _legacy_column_expr(column_name: str, legacy_columns: set[str]) -> str:
    if column_name in legacy_columns:
        return column_name
    defaults = {
        "datasource": f"'{DEFAULT_DATASOURCE}'",
        "source": "'inferred'",
        "confidence": "0.8",
        "fanout_risk": "'low'",
        "enabled": "1",
        "row_count": "0",
        "is_dimension": "0",
        "is_metric": "0",
    }
    return defaults.get(column_name, "NULL")
