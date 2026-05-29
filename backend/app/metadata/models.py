from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MetaTable(Base):
    __tablename__ = "meta_tables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    columns: Mapped[list["MetaColumn"]] = relationship(
        back_populates="table",
        cascade="all, delete-orphan",
        order_by="MetaColumn.id",
    )


class MetaColumn(Base):
    __tablename__ = "meta_columns"
    __table_args__ = (UniqueConstraint("table_id", "column_name", name="uq_meta_columns_table_column"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("meta_tables.id"), nullable=False)
    column_name: Mapped[str] = mapped_column(String, nullable=False)
    data_type: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_dimension: Mapped[bool] = mapped_column(Boolean, default=False)
    is_metric: Mapped[bool] = mapped_column(Boolean, default=False)
    sample_values: Mapped[str | None] = mapped_column(Text)

    table: Mapped[MetaTable] = relationship(back_populates="columns")


class MetaRelationship(Base):
    __tablename__ = "meta_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_table",
            "source_column",
            "target_table",
            "target_column",
            name="uq_meta_relationships_edge",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_table: Mapped[str] = mapped_column(String, nullable=False)
    source_column: Mapped[str] = mapped_column(String, nullable=False)
    target_table: Mapped[str] = mapped_column(String, nullable=False)
    target_column: Mapped[str] = mapped_column(String, nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, default="many_to_one")
    source: Mapped[str] = mapped_column(String, default="inferred")
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    fanout_risk: Mapped[str] = mapped_column(String, default="low")
    description: Mapped[str | None] = mapped_column(Text)


def create_metadata_schema(engine) -> None:
    Base.metadata.create_all(engine)
