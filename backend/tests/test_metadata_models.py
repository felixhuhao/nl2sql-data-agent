from sqlalchemy import create_engine, inspect

from backend.app.metadata.models import create_metadata_schema


def test_create_metadata_schema_includes_phase2_semantic_tables():
    engine = create_engine("sqlite:///:memory:")

    create_metadata_schema(engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "meta_metrics",
        "meta_column_aliases",
        "meta_verified_queries",
        "meta_analysis_spaces",
    }.issubset(table_names)
